# 2. A simulated service, and tuning kept away from the test

## Context

Measuring a rollout policy needs many deploys of known-good and known-bad versions into the same production
conditions, run under each policy. No real system offers that, so the platform runs against a simulator, and the
simulator's results are only as honest as the separation between tuning and testing.

## Decision

`fleet.py` simulates a service with three regions and four endpoints:

- Poisson traffic with a daily swing;
- binomial errors and log-normal latencies per endpoint;
- background incidents that hit both versions: a dependency failing in one region, a surge, a slow network path;
- a hidden regression in the new version, of seven kinds.

Harm is counted in expectation: the extra failed requests, plus the extra requests slower than three times the
endpoint's usual median.

Banks of 200 deploys, half of them bad:

| Bank | Seeds | Regression kinds | Use |
|---|---|---|---|
| Tuning worlds | 1000+ | errors, region errors, endpoint latency, tail stalls, leak | Choosing the settings; finding the bugs in ADR 1 |
| Held-out worlds | 5000+ | the same | Run once, after freezing |
| Held-back kinds | 9000+ | single-cell errors, bursty errors | Run once, after freezing; never seen in tuning |

The baselines get the same worlds, the same traffic and the same incidents. The service alert used by big-bang and
the threshold canary (error rate over 2x, or p99 over 1.5x, the pre-deploy level, twice running) is a common
production setting. A stricter alert would catch more and roll back more healthy versions.

## Consequences

- The gate in CI checks regression floors set a little below the measured numbers. The README reports the measured
  numbers.
- The simulator is kinder than production in some ways (regressions are steady, traffic is well-behaved) and harsher
  in others (incidents are frequent: about one deploy in two overlaps one). Absolute rates will differ in a real
  service. The comparison between policies is the point.
