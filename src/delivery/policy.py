"""Guardrails before a rollout starts: error budget, change freezes and change risk.

- **Error budget.** With a 99.5% availability objective over 28 days, the budget is 0.5% of requests. When it is
  spent, only fixes ship. When the last hour burns budget 14.4x faster than allowed (2% of the month's budget in an
  hour), nothing ships except a rollback.
- **Freezes.** Declared windows (a sales peak, a migration) block deploys unless marked as an emergency with an
  approver.
- **Risk.** Changes touching core modules, many modules, or shipped late on a Friday get a slower rollout
  (bake times doubled) and need an approver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .rollout import PROGRESSIVE

SLO = 0.995
FAST_BURN = 14.4


@dataclass(frozen=True)
class Change:
    service: str
    title: str
    kind: str  # feature | fix | rollback
    modules: tuple[str, ...]
    author: str
    at: datetime
    emergency: bool = False
    approver: str | None = None


@dataclass(frozen=True)
class Budget:
    requests_28d: int
    errors_28d: int
    requests_1h: int
    errors_1h: int

    @property
    def remaining(self) -> float:
        """Share of the 28-day error budget left (negative when overspent)."""
        allowed = (1 - SLO) * self.requests_28d
        return 1 - self.errors_28d / allowed if allowed else 1.0

    @property
    def burn_rate_1h(self) -> float:
        if not self.requests_1h:
            return 0.0
        return (self.errors_1h / self.requests_1h) / (1 - SLO)


@dataclass
class Decision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    risk: str = "standard"
    needs_approval: bool = False
    plan: tuple[tuple[float, int], ...] = PROGRESSIVE


def risk_of(c: Change) -> tuple[str, list[str]]:
    why = []
    if any(m.startswith("core/") for m in c.modules):
        why.append("touches a core module")
    if len(c.modules) >= 8:
        why.append(f"touches {len(c.modules)} modules")
    if c.at.weekday() == 4 and c.at.hour >= 15:
        why.append("Friday afternoon")
    return ("high" if why else "standard"), why


def decide(c: Change, budget: Budget, freezes: list[tuple[datetime, datetime, str]]) -> Decision:
    d = Decision(True)
    if c.kind == "rollback":
        d.reasons.append("rollbacks are always allowed")
        return d
    if budget.burn_rate_1h >= FAST_BURN:
        d.allowed = False
        d.reasons.append(
            f"the service is burning its error budget {budget.burn_rate_1h:.0f}x too fast; only "
            "rollbacks ship until it recovers"
        )
    elif budget.remaining <= 0 and c.kind != "fix":
        d.allowed = False
        d.reasons.append(f"the error budget is spent ({budget.remaining:.0%} left); only fixes ship")
    for start, end, name in freezes:
        if start <= c.at < end:
            if c.emergency and c.approver:
                d.reasons.append(f"emergency change during the {name} freeze, approved by {c.approver}")
            else:
                d.allowed = False
                d.reasons.append(f"{name} freeze until {end:%d %b %H:%M}")
    d.risk, why = risk_of(c)
    if d.risk == "high":
        d.needs_approval = True
        d.plan = tuple((f, b * 2) for f, b in PROGRESSIVE)
        d.reasons.append("high risk (" + ", ".join(why) + "): bake times doubled")
        if not c.approver:
            d.allowed = False
            d.reasons.append("high-risk change needs an approver")
    return d
