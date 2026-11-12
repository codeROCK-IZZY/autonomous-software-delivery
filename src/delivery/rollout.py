"""Rolling a new version out under three policies, and what each one costs when the version is bad or good.

- `progressive` (this platform): 1% -> 5% -> 25% -> 50% -> 95% holdback -> 100%, with the canary analysis every five
  minutes against control traffic served at the same time. The 95% step keeps 5% on the old version so there is
  still something to compare with after "promotion".
- `threshold_canary`: the same steps, but each step's canary error rate and p99 are compared with the service's
  numbers from the half hour before the deploy, on the whole service only. After 50% it goes to 100% and the
  service alert watches.
- `big_bang`: everyone gets the new version at once; the service alert (error rate over 2x, or p99 over 1.5x the
  pre-deploy level, for two checks running) rolls it back.

Every policy is run on the same world (same traffic, same background incidents) for the same 120 minutes after the
deploy. A rollback takes two minutes to take effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .canary import Finding, Settings, Window, analyse
from .fleet import Minute, Regression, World

PRE = 30  # minutes of history before the deploy
HORIZON = 120  # minutes simulated after the deploy (longer when a slower plan needs it)
ROLLBACK_DELAY = 2
LOOK_EVERY = 5

PROGRESSIVE = ((0.01, 15), (0.05, 15), (0.25, 15), (0.50, 15), (0.95, 30))
POLICIES = ("big_bang", "threshold_canary", "progressive")


@dataclass
class Outcome:
    policy: str
    regression: Regression
    rolled_back: bool = False
    rollback_minute: int | None = None  # minutes after the deploy
    max_fraction: float = 0.0  # largest share of traffic the new version reached
    promoted_minute: int | None = None  # when it reached 100%
    harm: float = 0.0  # requests harmed by the new version
    findings: list[Finding] = field(default_factory=list)
    reason: str = ""
    fraction: list[float] = field(default_factory=list)  # per minute after the deploy
    counts: list[tuple[int, int, int, int]] = field(
        default_factory=list
    )  # per minute: old n, old errors, new n, new errors


def _baseline(world: World) -> Window:
    w = Window()
    for t in range(PRE):
        w.add(world.minute(t, 0.0, Regression(), PRE))
    return w


def _record(o: Outcome, m_frac: float, minute: Minute) -> None:
    o.fraction.append(m_frac)
    n_old, n_new = (int(x) for x in minute.n.sum(axis=1))
    e_old, e_new = (int(x) for x in minute.errors.sum(axis=1))
    o.counts.append((n_old, e_old, n_new, e_new))
    o.harm += minute.bad_extra


def _alert(recent: Window, base: Window) -> str | None:
    """The service alert: error rate over 2x, or p99 over 1.5x, the pre-deploy level (whole service)."""
    n = recent.n.sum()
    e = recent.errors.sum()
    rate = e / max(n, 1)
    base_rate = base.error_rate(0)
    if rate > 2 * base_rate:
        return f"service error rate {rate:.2%} vs {base_rate:.2%} before the deploy"
    x = np.concatenate([recent.samples(0, None), recent.samples(1, None)])
    if len(x) and np.quantile(x, 0.99) > 1.5 * base.p99(0):
        return f"service p99 {np.quantile(x, 0.99):.0f} ms vs {base.p99(0):.0f} ms before the deploy"
    return None


def run(
    world_seed: int,
    reg: Regression,
    policy: str,
    cfg: Settings | None = None,
    plan: tuple[tuple[float, int], ...] | None = None,
) -> Outcome:
    cfg = cfg or Settings()
    world = World(world_seed)
    base = _baseline(world)
    o = Outcome(policy, reg)
    steps: list[tuple[float, int]]
    if policy == "progressive":
        steps = list(plan or PROGRESSIVE)
    elif policy == "threshold_canary":
        steps = [s for s in (plan or PROGRESSIVE) if s[0] < 0.9]
    else:
        steps = []
    frac = steps[0][0] if steps else 1.0
    step, step_start = 0, 0
    total = Window()  # since the deploy, canary vs control
    step_window = Window()
    recent: list[Window] = []
    alert_streak = 0
    rollback_at: int | None = None
    horizon = max(HORIZON, sum(b for _, b in steps) + 30)
    for k in range(horizon):
        t = PRE + k
        if rollback_at is not None and k >= rollback_at + ROLLBACK_DELAY:
            frac = 0.0
        m = world.minute(t, frac, reg, PRE)
        _record(o, frac, m)
        o.max_fraction = max(o.max_fraction, frac)
        if frac == 0.0 or rollback_at is not None:
            continue
        total.add(m)
        step_window.add(m)
        mw = Window()
        mw.add(m)
        recent = [*recent[-9:], mw]
        elapsed = k + 1 - step_start
        in_steps = step < len(steps)
        if in_steps and policy == "progressive" and elapsed % LOOK_EVERY == 0:
            # only this step: the traffic split is constant within it, so the comparison is fair (see ADR 1)
            found = analyse(step_window, cfg)
            if found:
                rollback_at, o.findings = k, found
                o.reason = "canary analysis: " + "; ".join(f.describe() for f in found[:3])
                continue
        if in_steps and elapsed >= steps[step][1]:
            if policy == "threshold_canary":
                reason = _threshold_check(step_window, base)
                if reason:
                    rollback_at, o.reason = k, "threshold canary: " + reason
                    continue
            step += 1
            step_start = k + 1
            step_window = Window()
            if step < len(steps):
                frac = steps[step][0]
            else:
                frac = 1.0
                o.promoted_minute = k + 1
            continue
        if not in_steps and policy != "progressive" and (k + 1) % LOOK_EVERY == 0:
            win = Window()
            for r in recent:
                win.n += r.n
                win.errors += r.errors
                for v in range(2):
                    for s in range(len(win.latency[v])):
                        win.latency[v][s].extend(r.latency[v][s])
            reason = _alert(win, base)
            alert_streak = alert_streak + 1 if reason else 0
            if alert_streak >= 2 and reason:
                rollback_at, o.reason = k, "service alert: " + reason
    if policy == "big_bang":
        o.promoted_minute = 0
    if rollback_at is not None:
        o.rolled_back, o.rollback_minute = True, rollback_at
        o.promoted_minute = o.promoted_minute if o.promoted_minute is not None else None
    return o


def _threshold_check(w: Window, base: Window) -> str | None:
    rate, base_rate = w.error_rate(1), base.error_rate(0)
    if rate > 1.5 * base_rate + 0.001:
        return f"canary error rate {rate:.2%} vs {base_rate:.2%} before the deploy"
    p99, base_p99 = w.p99(1), base.p99(0)
    if p99 > 1.3 * base_p99:
        return f"canary p99 {p99:.0f} ms vs {base_p99:.0f} ms before the deploy"
    return None
