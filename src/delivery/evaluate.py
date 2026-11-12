"""Evaluation: bad and good deploys under three rollout policies, plus test selection.

    deliver evaluate [--deploys N] [--gate] [--out DIR]

Deploy banks (half the versions are bad):
- `dev` (world seeds 1000+): the canary settings were tuned here, on the tuning regression kinds;
- `heldout` (5000+): same kinds, worlds never run during tuning;
- `fresh` (9000+): regression kinds held back from tuning (one endpoint in one region; failures in bursts).
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from .canary import Settings
from .fleet import FRESH_KINDS, TUNING_KINDS, Regression, sample_regression
from .rollout import POLICIES, Outcome, run

BANKS = {"dev": (1000, TUNING_KINDS), "heldout": (5000, TUNING_KINDS), "fresh": (9000, FRESH_KINDS)}


def deploys(bank: str, n: int) -> list[tuple[int, Regression]]:
    base, kinds = BANKS[bank]
    out = []
    for i in range(n):
        rng = np.random.default_rng(base + i)
        out.append((base + i, sample_regression(rng, kinds)))
    return out


def _job(args: tuple[int, Regression, str, Settings]) -> Outcome:
    seed, reg, policy, cfg = args
    o = run(seed, reg, policy, cfg)
    o.counts, o.fraction = [], []  # keep results small
    return o


def simulate(
    bank: str, n: int, cfg: Settings, policies: tuple[str, ...] = POLICIES, workers: int | None = None
) -> dict[str, list[Outcome]]:
    jobs = [(seed, reg, p, cfg) for seed, reg in deploys(bank, n) for p in policies]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        outs = list(pool.map(_job, jobs, chunksize=4))
    return {p: [o for o in outs if o.policy == p] for p in policies}


def summarise(outs: list[Outcome]) -> dict[str, Any]:
    bad = [o for o in outs if o.regression.bad]
    good = [o for o in outs if not o.regression.bad]
    caught = [o for o in bad if o.rolled_back]
    early = [o for o in caught if o.max_fraction <= 0.05]
    promoted = [o.promoted_minute for o in good if not o.rolled_back and o.promoted_minute is not None]
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted({o.regression.kind for o in bad}):
        ks = [o for o in bad if o.regression.kind == kind]
        by_kind[kind] = {
            "deploys": len(ks),
            "caught": sum(o.rolled_back for o in ks),
            "harm": round(sum(o.harm for o in ks)),
        }
    return {
        "bad": len(bad),
        "good": len(good),
        "caught": len(caught),
        "caught_share": round(len(caught) / max(len(bad), 1), 4),
        "caught_by_5pct": len(early),
        "caught_by_5pct_share": round(len(early) / max(len(bad), 1), 4),
        "bad_shipped": len(bad) - len(caught),
        "harm": round(sum(o.harm for o in bad)),
        "harm_per_bad": round(sum(o.harm for o in bad) / max(len(bad), 1), 1),
        "false_rollbacks": sum(o.rolled_back for o in good),
        "false_rollback_share": round(sum(o.rolled_back for o in good) / max(len(good), 1), 4),
        "minutes_to_full": float(np.median(promoted)) if promoted else None,
        "by_kind": by_kind,
        "examples": [{"regression": o.regression.describe(), "reason": o.reason} for o in good if o.rolled_back][:5],
    }


def tune(n: int, grid: list[Settings]) -> list[dict[str, Any]]:
    """The grid search on `dev` that chose the settings (re-runnable: `deliver tune`)."""
    rows = []
    for cfg in grid:
        s = summarise(simulate("dev", n, cfg, ("progressive",))["progressive"])
        rows.append(
            {
                "settings": asdict(cfg),
                **{k: s[k] for k in ("caught_share", "caught_by_5pct_share", "harm", "false_rollbacks")},
            }
        )
    return rows


# Regression floors for CI, set below the measured held-out and fresh numbers; the report shows the measured ones.
GATE: dict[str, float] = {
    "heldout_caught_share_min": 0.95,
    "fresh_caught_share_min": 0.93,
    "false_rollback_share_max": 0.02,
    "harm_vs_big_bang_max": 0.10,  # progressive harm as a share of big-bang harm, held-out
    "impact_bugs_caught_share_min": 0.99,
}


def run_all(n: int = 200, cfg: Settings | None = None) -> dict[str, Any]:
    from .selection import evaluate_repos

    cfg = cfg or Settings()
    banks = {b: {p: summarise(o) for p, o in simulate(b, n, cfg).items()} for b in BANKS}
    selection = evaluate_repos()
    h, f = banks["heldout"], banks["fresh"]
    good = [banks[b]["progressive"] for b in ("heldout", "fresh")]
    checks = {
        "heldout_caught_share_min": h["progressive"]["caught_share"],
        "fresh_caught_share_min": f["progressive"]["caught_share"],
        "false_rollback_share_max": round(
            sum(g["false_rollbacks"] for g in good) / max(sum(g["good"] for g in good), 1), 4
        ),
        "harm_vs_big_bang_max": round(h["progressive"]["harm"] / max(h["big_bang"]["harm"], 1), 4),
        "impact_bugs_caught_share_min": selection["impact"]["bugs_caught_share"],  # type: ignore[index]
    }
    passed = {k: (v <= GATE[k] if k.endswith("_max") else v >= GATE[k]) for k, v in checks.items()}
    return {
        "deploys_per_bank": n,
        "settings": asdict(cfg),
        "banks": banks,
        "selection": selection,
        "checks": checks,
        "gate": GATE,
        "passed": passed,
        "ok": all(passed.values()),
    }


def save(result: dict[str, Any], out: Path) -> None:
    from .report import html_report, markdown

    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    (out / "report.md").write_text(markdown(result), encoding="utf-8", newline="\n")
    (out / "report.html").write_text(html_report(result), encoding="utf-8", newline="\n")
