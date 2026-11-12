"""A simulated production service: traffic by region and endpoint, errors, latency, background incidents, and the
new version's regression (if any).

Each minute, every segment (region x endpoint) serves Poisson traffic split between the old and the new version.
Errors are binomial; latencies are sampled from a log-normal per endpoint. Background incidents hit both versions
alike (a dependency failing in one region, a traffic surge, a slow network path), which is what makes a
before-and-after comparison unreliable and a side-by-side comparison necessary.

Regression kinds used while tuning the canary analysis: `errors`, `region_errors`, `endpoint_latency`,
`tail_latency`, `leak`. Kinds kept back for the test: `cell_errors` (one endpoint in one region) and
`bursty_errors` (failures in short bursts).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

REGIONS = ("eu-west", "us-east", "ap-south")
ENDPOINTS = ("checkout", "search", "login", "catalog")
SEGMENTS = [(r, e) for r in REGIONS for e in ENDPOINTS]
N_SEG = len(SEGMENTS)
LATENCY_SAMPLE_RATE = 0.10  # share of requests whose latency is recorded, the same for both versions

TUNING_KINDS = ("errors", "region_errors", "endpoint_latency", "tail_latency", "leak")
FRESH_KINDS = ("cell_errors", "bursty_errors")


@dataclass(frozen=True)
class Regression:
    kind: str = "none"
    factor: float = 1.0  # error multiplier, or latency multiplier for latency kinds
    region: str | None = None
    endpoint: str | None = None

    @property
    def bad(self) -> bool:
        return self.kind != "none"

    def describe(self) -> str:
        where = " ".join(x for x in (self.endpoint, self.region) if x)
        return f"{self.kind} x{self.factor:.1f}" + (f" ({where})" if where else "")


@dataclass(frozen=True)
class Incident:
    """Background trouble affecting both versions."""

    kind: str  # dependency | surge | slow_path
    start: int
    end: int
    region: str | None
    magnitude: float


@dataclass
class Minute:
    n: np.ndarray  # (2, N_SEG) requests: [old, new]
    errors: np.ndarray  # (2, N_SEG)
    latency: list[list[np.ndarray]]  # [version][segment] -> samples in ms
    bad_extra: float  # requests harmed by the new version beyond what the old one would have done


@dataclass
class World:
    seed: int
    rate: np.ndarray = field(init=False)
    err: np.ndarray = field(init=False)
    lat_mu: np.ndarray = field(init=False)
    slo_ms: np.ndarray = field(init=False)
    incidents: list[Incident] = field(init=False)
    rng: np.random.Generator = field(init=False)

    LAT_SIGMA = 0.45

    def __post_init__(self) -> None:
        g = np.random.default_rng(self.seed)
        base = {"checkout": 260, "search": 900, "login": 380, "catalog": 620}
        share = {"eu-west": 1.0, "us-east": 1.3, "ap-south": 0.6}
        self.rate = np.array([base[e] * share[r] for r, e in SEGMENTS]) * g.uniform(0.8, 1.2, N_SEG)
        self.err = np.array(
            [{"checkout": 0.004, "search": 0.002, "login": 0.006, "catalog": 0.003}[e] for _, e in SEGMENTS]
        ) * g.uniform(0.7, 1.4, N_SEG)
        med = {"checkout": 180.0, "search": 90.0, "login": 120.0, "catalog": 60.0}
        self.lat_mu = np.log(np.array([med[e] for _, e in SEGMENTS]) * g.uniform(0.85, 1.15, N_SEG))
        self.slo_ms = np.exp(self.lat_mu) * 3.0  # a request slower than 3x the usual median counts as harmed
        self.incidents = []
        horizon = 150
        if g.random() < 0.45:
            s = int(g.integers(0, horizon - 20))
            self.incidents.append(
                Incident("dependency", s, s + int(g.integers(6, 16)), str(g.choice(REGIONS)), float(g.uniform(3, 8)))
            )
        if g.random() < 0.35:
            s = int(g.integers(0, horizon - 30))
            self.incidents.append(Incident("surge", s, s + int(g.integers(15, 30)), None, float(g.uniform(1.5, 2.2))))
        if g.random() < 0.3:
            s = int(g.integers(0, horizon - 20))
            self.incidents.append(
                Incident("slow_path", s, s + int(g.integers(8, 20)), str(g.choice(REGIONS)), float(g.uniform(1.3, 1.8)))
            )
        self.rng = np.random.default_rng(self.seed + 1_000_003)

    def _background(self, t: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Traffic, error and latency multipliers from background incidents at minute `t` (both versions)."""
        traffic = np.ones(N_SEG)
        err = np.ones(N_SEG)
        lat = np.zeros(N_SEG)
        for inc in self.incidents:
            if not inc.start <= t < inc.end:
                continue
            mask = np.array([inc.region is None or r == inc.region for r, _ in SEGMENTS])
            if inc.kind == "dependency":
                err[mask] *= inc.magnitude
            elif inc.kind == "surge":
                traffic *= inc.magnitude
                lat += math.log(1 + 0.12 * (inc.magnitude - 1))  # busier means a little slower
            else:
                lat[mask] += math.log(inc.magnitude)
        return traffic, err, lat

    def _regression(self, reg: Regression, since: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Error multiplier, log-latency shift and stalled-request share for new-version traffic, `since` minutes after
        the deploy."""
        err = np.ones(N_SEG)
        lat = np.zeros(N_SEG)
        tail = np.zeros(N_SEG)
        if not reg.bad:
            return err, lat, tail
        mask = np.array(
            [
                (reg.region is None or r == reg.region) and (reg.endpoint is None or e == reg.endpoint)
                for r, e in SEGMENTS
            ]
        )
        if reg.kind in ("errors", "region_errors", "cell_errors"):
            err[mask] = reg.factor
        elif reg.kind == "bursty_errors":
            if since % 10 < 2:  # two bad minutes in every ten
                err[mask] = reg.factor
        elif reg.kind == "leak":
            err[mask] = 1 + (reg.factor - 1) * min(since / 60, 1.0)  # gets worse as memory fills
        elif reg.kind == "endpoint_latency":
            lat[mask] = math.log(reg.factor)
        elif reg.kind == "tail_latency":
            tail[mask] = 0.04  # 4% of requests stall
        return err, lat, tail

    def minute(self, t: int, frac_new: float, reg: Regression, deployed_at: int) -> Minute:
        g = self.rng
        traffic, bg_err, bg_lat = self._background(t)
        diurnal = 1 + 0.15 * math.sin(2 * math.pi * t / 1440)
        lam = self.rate * traffic * diurnal
        n_new = g.poisson(lam * frac_new) if frac_new > 0 else np.zeros(N_SEG, dtype=np.int64)
        n_old = g.poisson(lam * (1 - frac_new)) if frac_new < 1 else np.zeros(N_SEG, dtype=np.int64)
        p_old = np.minimum(self.err * bg_err, 0.5)
        r_err, r_lat, tail = self._regression(reg, t - deployed_at)
        p_new = np.minimum(p_old * r_err, 0.9)
        e_old = g.binomial(n_old, p_old)
        e_new = g.binomial(n_new, p_new)
        mu_old = self.lat_mu + bg_lat
        mu_new = mu_old + r_lat
        lat: list[list[np.ndarray]] = [[], []]
        for v, (n, mu) in enumerate(((n_old, mu_old), (n_new, mu_new))):
            for s in range(N_SEG):
                k = int(g.binomial(n[s], LATENCY_SAMPLE_RATE))  # in proportion to traffic, never capped
                x = np.exp(g.normal(mu[s], self.LAT_SIGMA, k))
                if v == 1 and tail[s] and k:
                    stall = g.random(k) < tail[s]
                    x[stall] += g.uniform(800, 2500, int(stall.sum()))
                lat[v].append(x)
        # harm: extra failed requests plus extra requests slower than the SLO, in expectation
        slow_old = _p_slow(mu_old, self.LAT_SIGMA, self.slo_ms)
        slow_new = _p_slow(mu_new, self.LAT_SIGMA, self.slo_ms)
        slow_new = slow_new + tail * (1 - slow_new)
        extra = float(
            np.sum(n_new * (p_new - p_old)) + np.sum(n_new * (1 - p_new) * np.maximum(slow_new - slow_old, 0))
        )
        return Minute(np.vstack([n_old, n_new]), np.vstack([e_old, e_new]), lat, max(extra, 0.0))


def _p_slow(mu: np.ndarray, sigma: float, slo: np.ndarray) -> np.ndarray:
    z = (np.log(slo) - mu) / sigma
    return np.array([0.5 * math.erfc(v / math.sqrt(2)) for v in z])


def sample_regression(rng: np.random.Generator, kinds: tuple[str, ...], bad_share: float = 0.5) -> Regression:
    if rng.random() >= bad_share:
        return Regression()
    kind = str(rng.choice(kinds))
    region = str(rng.choice(REGIONS))
    endpoint = str(rng.choice(ENDPOINTS))
    if kind == "errors":
        return Regression(kind, float(rng.uniform(1.6, 4.0)))
    if kind == "region_errors":
        return Regression(kind, float(rng.uniform(2.0, 6.0)), region=region)
    if kind == "cell_errors":
        return Regression(kind, float(rng.uniform(3.0, 8.0)), region=region, endpoint=endpoint)
    if kind == "bursty_errors":
        return Regression(kind, float(rng.uniform(6.0, 15.0)))
    if kind == "leak":
        return Regression(kind, float(rng.uniform(3.0, 6.0)))
    if kind == "endpoint_latency":
        return Regression(kind, float(rng.uniform(1.25, 1.8)), endpoint=endpoint)
    if kind == "tail_latency":
        return Regression(kind, 1.0, endpoint=endpoint)
    raise ValueError(kind)
