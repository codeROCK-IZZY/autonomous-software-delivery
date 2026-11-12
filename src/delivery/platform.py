"""The delivery pipeline end to end: guardrails, test selection, progressive rollout, rollback note.

One `Pipeline.ship` call takes a change through every stage and records what happened, which is what the app shows.
The production service is the simulator in `fleet.py`; the version's hidden regression (if any) is the one input a
real system would not have.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from .fleet import Regression
from .notes import Note, Writer, plain_note
from .policy import Budget, Change, Decision, decide
from .rollout import Outcome, run
from .selection import Repo, select

SERVICES = {"checkout-api": 3, "search-api": 5, "accounts-api": 8}  # service -> world seed offset


@dataclass
class Deploy:
    id: str
    change: Change
    decision: Decision
    tests_selected: int
    tests_total: int
    outcome: Outcome | None
    note: Note | None
    regression: Regression
    started: datetime

    @property
    def status(self) -> str:
        if self.outcome is None:
            return "blocked"
        return "rolled back" if self.outcome.rolled_back else "promoted"


@dataclass
class Pipeline:
    repo: Repo = field(default_factory=lambda: Repo(21))
    writer: Writer | None = None
    freezes: list[tuple[datetime, datetime, str]] = field(default_factory=list)
    deploys: dict[str, Deploy] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def budget(self, service: str) -> Budget:
        # a healthy month by default; `accounts-api` has spent its budget in the demo
        if service == "accounts-api":
            return Budget(40_000_000, 214_000, 90_000, 380)
        return Budget(40_000_000, 61_000, 90_000, 260)

    def ship(self, change: Change, regression: Regression, seed: int) -> Deploy:
        decision = decide(change, self.budget(change.service), self.freezes)
        ids = {m: i for i, m in enumerate(self.repo.modules)}
        changed = {ids[m] for m in change.modules if m in ids}
        chosen = select(self.repo, changed, "impact") if changed else []
        outcome: Outcome | None = None
        note: Note | None = None
        if decision.allowed:
            outcome = run(
                seed + SERVICES.get(change.service, 0) * 10_000, regression, "progressive", plan=decision.plan
            )
            if outcome.rolled_back:
                note = self.writer.write(outcome, change.service) if self.writer else plain_note(outcome)
        with self._lock:
            did = f"D-{len(self.deploys) + 1:04d}"
            d = Deploy(did, change, decision, len(chosen), len(self.repo.tests), outcome, note, regression, change.at)
            self.deploys[did] = d
        return d


def demo(writer: Writer | None = None, now: datetime | None = None) -> Pipeline:
    """A week of deploys across three services, with every outcome represented."""
    now = now or datetime(2026, 11, 12, 16, 0)
    p = Pipeline(writer=writer)
    p.freezes = [(now + timedelta(days=12), now + timedelta(days=16), "Black Friday")]
    m = p.repo.modules
    apps = [x for x in m if x.startswith("apps/")]
    services = [x for x in m if x.startswith("services/")]
    core = [x for x in m if x.startswith("core/")]
    g = np.random.default_rng(4)
    plan = [
        ("checkout-api", "Apple Pay button on the basket page", "feature", Regression(), 0),
        (
            "search-api",
            "Rank in-stock items first",
            "feature",
            Regression("endpoint_latency", 1.5, endpoint="search"),
            0,
        ),
        ("checkout-api", "Retry card declines once", "feature", Regression("region_errors", 4.0, region="us-east"), 0),
        ("accounts-api", "Password rules from the new policy", "feature", Regression(), 0),
        ("accounts-api", "Fix session expiry off-by-one", "fix", Regression(), 0),
        ("search-api", "Upgrade the HTTP client", "feature", Regression("tail_latency", 1.0, endpoint="catalog"), 0),
        ("checkout-api", "Shared money type rounding", "feature", Regression(), 1),
        ("search-api", "Synonyms for brand names", "feature", Regression(), 0),
        ("checkout-api", "Cache delivery slots", "feature", Regression("cell_errors", 6.0, "eu-west", "checkout"), 0),
        ("search-api", "Spelling suggestions", "feature", Regression(), 0),
    ]
    authors = ["a.okafor", "j.mensah", "s.patel", "l.novak", "r.silva"]
    for i, (svc, title, kind, reg, touches_core) in enumerate(plan):
        at = now - timedelta(days=len(plan) - i, hours=int(g.integers(0, 6)))
        mods = [str(g.choice(apps)), str(g.choice(services))] + ([str(g.choice(core))] if touches_core else [])
        approver = "platform-lead" if touches_core else None
        change = Change(svc, title, kind, tuple(mods), authors[i % len(authors)], at, approver=approver)
        p.ship(change, reg, 300 + i)
    return p
