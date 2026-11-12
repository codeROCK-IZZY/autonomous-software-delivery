"""The simulator, the canary statistics, rollout policies, guardrails and test selection."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from delivery.canary import GROUPS, Settings, Window, analyse
from delivery.fleet import N_SEG, SEGMENTS, Regression, World, sample_regression
from delivery.policy import Budget, Change, decide
from delivery.rollout import PROGRESSIVE, run
from delivery.selection import Repo, evaluate, select


def window(world: World, minutes: int, frac: float, reg: Regression, start: int = 0) -> Window:
    w = Window()
    for t in range(start, start + minutes):
        w.add(world.minute(t, frac, reg, start))
    return w


def test_world_is_reproducible_and_splits_traffic() -> None:
    a, b = World(5), World(5)
    ma, mb = a.minute(3, 0.25, Regression(), 0), b.minute(3, 0.25, Regression(), 0)
    assert np.array_equal(ma.n, mb.n)
    share = ma.n[1].sum() / ma.n.sum()
    assert 0.2 < share < 0.3
    assert ma.bad_extra == 0.0


def test_regressions_hit_only_their_segments() -> None:
    w = World(9)
    w.incidents = []
    reg = Regression("cell_errors", 8.0, region="eu-west", endpoint="checkout")
    win = window(w, 30, 0.5, reg)
    cell = SEGMENTS.index(("eu-west", "checkout"))
    rate = win.errors[1] / np.maximum(win.n[1], 1)
    base = win.errors[0] / np.maximum(win.n[0], 1)
    assert rate[cell] > 4 * base[cell]
    others = [i for i in range(N_SEG) if i != cell]
    assert np.all(rate[others] < 2.5 * np.maximum(base[others], 1e-3))


def test_groups_cover_service_regions_endpoints_and_cells() -> None:
    labels = [g for g, _ in GROUPS]
    assert labels[0] == "whole service" and "us-east" in labels and "search" in labels and "ap-south/login" in labels
    assert len(GROUPS) == 1 + 3 + 4 + N_SEG


def test_healthy_version_is_not_flagged_even_during_an_incident() -> None:
    w = World(17)
    w.incidents = []
    from delivery.fleet import Incident

    w.incidents.append(Incident("dependency", 0, 15, "us-east", 6.0))
    assert analyse(window(w, 15, 0.25, Regression()), Settings()) == []


def test_each_kind_of_regression_is_found() -> None:
    for reg, metric in (
        (Regression("region_errors", 5.0, region="ap-south"), "errors"),
        (Regression("endpoint_latency", 1.6, endpoint="search"), "latency"),
        (Regression("tail_latency", 1.0, endpoint="search"), "tail"),  # low-traffic endpoints: see limitations
    ):
        w = World(23)
        w.incidents = []
        found = analyse(window(w, 15, 0.25, reg), Settings())
        assert any(f.metric == metric for f in found), (reg, found)


def test_progressive_rolls_back_early_and_big_bang_harms_more() -> None:
    reg = Regression("region_errors", 5.0, region="us-east")
    ours, bang = run(31, reg, "progressive"), run(31, reg, "big_bang")
    assert ours.rolled_back and ours.max_fraction <= 0.25
    assert bang.max_fraction == 1.0
    assert ours.harm < bang.harm / 3
    good = run(31, Regression(), "progressive")
    assert not good.rolled_back and good.promoted_minute == sum(b for _, b in PROGRESSIVE)
    assert good.fraction[-1] == 1.0


def test_slower_plan_is_respected() -> None:
    plan = tuple((f, b * 2) for f, b in PROGRESSIVE)
    o = run(31, Regression(), "progressive", plan=plan)
    assert o.promoted_minute == sum(b for _, b in plan)


def test_regressions_sampled_per_kind() -> None:
    g = np.random.default_rng(1)
    regs = [sample_regression(g, ("cell_errors",), bad_share=1.0) for _ in range(5)]
    assert all(r.kind == "cell_errors" and r.region and r.endpoint for r in regs)


def test_guardrails() -> None:
    now = datetime(2026, 11, 12, 10)
    healthy = Budget(40_000_000, 61_000, 90_000, 260)
    spent = Budget(40_000_000, 214_000, 90_000, 380)
    burning = Budget(40_000_000, 61_000, 90_000, 9_000)
    feat = Change("checkout-api", "x", "feature", ("apps/app0/m110",), "a", now)
    assert decide(feat, healthy, []).allowed
    assert not decide(feat, spent, []).allowed
    assert decide(Change("checkout-api", "x", "fix", ("apps/app0/m110",), "a", now), spent, []).allowed
    assert not decide(feat, burning, []).allowed
    assert decide(Change("checkout-api", "x", "rollback", (), "a", now), burning, []).allowed
    freeze = [(now - timedelta(hours=1), now + timedelta(days=1), "Black Friday")]
    assert not decide(feat, healthy, freeze).allowed
    emergency = Change("checkout-api", "x", "fix", ("apps/app0/m110",), "a", now, emergency=True, approver="lead")
    assert decide(emergency, healthy, freeze).allowed
    core = Change("checkout-api", "x", "feature", ("core/core0/m1",), "a", now)
    d = decide(core, healthy, [])
    assert d.risk == "high" and not d.allowed and d.needs_approval
    friday = Change("checkout-api", "x", "feature", ("apps/app0/m110",), "a", datetime(2026, 11, 13, 16), approver="b")
    d = decide(friday, healthy, [])
    assert d.allowed and d.plan[0][1] == 2 * PROGRESSIVE[0][1]


def test_selection() -> None:
    repo = Repo(21)
    leaf = max(range(len(repo.modules)), key=lambda i: repo.layer[i])
    chosen = select(repo, {leaf}, "impact")
    assert chosen and len(chosen) < len(repo.tests)
    assert all(repo.tests[t] in repo.dependents({leaf}) for t in chosen)
    core = repo.layer.index(0)
    assert len(select(repo, {core}, "impact")) == len(repo.tests)  # a core change runs everything
    r = evaluate(21, changes=300)
    imp, alldir = r["impact"], r["same_directory"]
    assert isinstance(imp, dict) and isinstance(alldir, dict)
    assert imp["bugs_caught_share"] > alldir["bugs_caught_share"]
    assert imp["tests_run_share"] < 0.6
