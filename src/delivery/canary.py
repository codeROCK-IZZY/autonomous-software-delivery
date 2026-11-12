"""Canary analysis: is the new version worse than the old one, measured side by side over the same minutes?

Comparing the canary with the control traffic served *at the same time* cancels whatever else is happening (a
dependency failing, a traffic surge), which a comparison with last hour's numbers cannot do. Each region, each
endpoint and each cell (region x endpoint) is tested as well as the whole service, because a regression in one
region is invisible once diluted across all traffic.

Three tests per group, each needing statistical evidence *and* a material effect:
- errors: exact conditional binomial test of the canary's share of all errors against its share of traffic;
  the canary's error rate must also be at least 1.25x the control's;
- latency: Mann-Whitney U (canary slower) on sampled latencies; the canary median must be at least 1.10x;
- tail: requests slower than the 99th percentile of both versions pooled; the canary's share of those slow
  requests is tested against its share of the samples, and at least 3% of canary requests (and twice the control's
  share) must be that slow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

from .fleet import ENDPOINTS, N_SEG, REGIONS, SEGMENTS, Minute

# The whole service, each region (a bad deploy target), each endpoint (a bad code path) and each cell.
GROUPS: list[tuple[str, list[int]]] = (
    [("whole service", list(range(N_SEG)))]
    + [(r, [i for i, (rr, _) in enumerate(SEGMENTS) if rr == r]) for r in REGIONS]
    + [(e, [i for i, (_, ee) in enumerate(SEGMENTS) if ee == e]) for e in ENDPOINTS]
    + [("/".join(seg), [i]) for i, seg in enumerate(SEGMENTS)]
)


@dataclass(frozen=True)
class Settings:
    family_alpha: float = 0.01  # chance of a false rollback per deploy, spread over every test and look
    looks: int = 18  # looks planned per rollout (for the spread)
    min_error_ratio: float = 1.25
    min_latency_ratio: float = 1.10
    min_tail_share: float = 0.02
    min_canary_samples: int = 15

    @property
    def alpha(self) -> float:
        return self.family_alpha / (self.looks * len(GROUPS) * 3)


@dataclass(frozen=True)
class Finding:
    metric: str  # errors | latency | tail
    segment: str  # "whole service", a region, an endpoint or "region/endpoint"
    canary: float
    control: float
    p_value: float
    canary_requests: int

    def describe(self) -> str:
        if self.metric == "errors":
            return (
                f"{self.segment}: error rate {self.canary:.2%} on the new version vs {self.control:.2%} "
                f"(p={self.p_value:.1e})"
            )
        if self.metric == "latency":
            return (
                f"{self.segment}: median latency {self.canary:.0f} ms vs {self.control:.0f} ms (p={self.p_value:.1e})"
            )
        return (
            f"{self.segment}: {self.canary:.1%} of new-version requests slower than {self.control:.0f} ms, "
            f"the p99 of both versions (p={self.p_value:.1e})"
        )


@dataclass
class Window:
    """Traffic accumulated for both versions over a run of minutes."""

    n: np.ndarray = field(default_factory=lambda: np.zeros((2, N_SEG), dtype=np.int64))
    errors: np.ndarray = field(default_factory=lambda: np.zeros((2, N_SEG), dtype=np.int64))
    latency: list[list[list[np.ndarray]]] = field(
        default_factory=lambda: [[[] for _ in range(N_SEG)] for _ in range(2)]
    )

    def add(self, m: Minute) -> None:
        self.n += m.n
        self.errors += m.errors
        for v in range(2):
            for s in range(N_SEG):
                if len(m.latency[v][s]):
                    self.latency[v][s].append(m.latency[v][s])

    def samples(self, v: int, segs: list[int] | None = None) -> np.ndarray:
        parts = [x for seg in (range(N_SEG) if segs is None else segs) for x in self.latency[v][seg]]
        return np.concatenate(parts) if parts else np.zeros(0)

    def error_rate(self, v: int) -> float:
        return float(self.errors[v].sum() / max(self.n[v].sum(), 1))

    def p99(self, v: int) -> float:
        x = self.samples(v, None)
        return float(np.quantile(x, 0.99)) if len(x) else 0.0


def analyse(w: Window, cfg: Settings) -> list[Finding]:
    found: list[Finding] = []
    for label, segs in GROUPS:
        cn, bn = int(w.n[1][segs].sum()), int(w.n[0][segs].sum())
        ce, be = int(w.errors[1][segs].sum()), int(w.errors[0][segs].sum())
        if cn == 0 or bn == 0:
            continue
        if ce > 0:
            p = float(stats.binom.sf(ce - 1, ce + be, cn / (cn + bn)))
            rc, rb = ce / cn, be / bn
            if p < cfg.alpha and rc >= cfg.min_error_ratio * max(rb, 1e-9):
                found.append(Finding("errors", label, rc, rb, p, cn))
        c, b = w.samples(1, segs), w.samples(0, segs)
        if len(c) < cfg.min_canary_samples or len(b) < cfg.min_canary_samples:
            continue
        mc, mb = float(np.median(c)), float(np.median(b))
        if mc >= cfg.min_latency_ratio * mb:
            p = float(stats.mannwhitneyu(c, b, alternative="greater").pvalue)
            if p < cfg.alpha:
                found.append(Finding("latency", label, mc, mb, p, cn))
        # both groups against the pooled 99th percentile: a small control sample's own p99 is too noisy to test
        # against as if it were exact
        q = float(np.quantile(np.concatenate([c, b]), 0.99))
        kc, kb = int((c > q).sum()), int((b > q).sum())
        share, base_share = kc / len(c), kb / len(b)
        if kc and share >= cfg.min_tail_share and share >= 2 * base_share:
            p = float(stats.binom.sf(kc - 1, kc + kb, len(c) / (len(c) + len(b))))
            if p < cfg.alpha:
                found.append(Finding("tail", label, share, q, p, cn))
    return found
