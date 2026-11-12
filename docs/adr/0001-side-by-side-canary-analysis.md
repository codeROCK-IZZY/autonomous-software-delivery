# 1. Canary analysis: side by side, per segment, within a step

## Context

A rollout has to decide, every few minutes and with little data, whether the new version is worse than the old
one. Two kinds of mistake cost money. Shipping a bad version harms users. Rolling back a good one wastes a
release and teaches people to override the system.

## Decision

- **Compare against the old version serving traffic over the same minutes**, not against the service's history.
  Background incidents (a dependency failing, a traffic surge) then affect both sides equally.
- **Test the whole service, each region, each endpoint and each region-endpoint cell.** A regression in one cell is
  1/12 of the traffic and disappears in whole-service numbers.
- **Three tests per group:**
  - errors: an exact conditional binomial test (given all errors, is the canary's share larger than its share of
    traffic?);
  - latency: a one-sided Mann-Whitney U test;
  - tail: each version's share of requests above the *pooled* p99, tested the same way.
- **Require a material effect as well as significance:** 1.25x the error rate, 1.10x the median, or 2% of requests
  stalled. At high traffic, tiny real differences are significant without mattering.
- **Spread a 1% family-wise false-alarm rate** over every group, test and planned look (Bonferroni).
- **Analyse within the current step only.**

## Two bugs that tuning exposed

1. **Pooling across steps (Simpson's paradox).** The first version accumulated canary and control data from the
   start of the rollout. The traffic split changes between steps (1%, 5%, 25%...), so minutes from late steps
   dominate the canary while early minutes dominate the control. An incident during the 50% step therefore landed
   mostly on the canary's side. The result was 21% false rollbacks, with p-values like 1e-19 on healthy versions.
   Analysing each step separately, where the split is constant, fixed it.
2. **Testing against a noisy baseline as if it were exact.** The first tail test counted canary requests above the
   control's p99 and tested that against 1%. At the 95% holdback step the control is small, so its p99 is noisy,
   and every healthy version was flagged. The pooled p99 is a symmetric function of both samples, which makes the
   conditional test exact under the null.

A third fix belonged to the simulator. Latency samples were capped per minute, which over-weighted busy minutes on
the canary side. Samples are now taken in proportion to traffic for both versions.

## Tuning

On the tuning worlds only. Cell-level tests alone caught 84% of bad versions. Adding region and endpoint groups
raised that to 99% with no false rollbacks at a 1% family-wise rate; a 5% or 20% rate added false rollbacks for
little gain (see `deliver tune`). The settings were then frozen.

## Consequences

- Ninety minutes to full rollout. The early steps have little traffic, so a modest regression usually shows at
  5-25%.
- The tail test loses power once the canary carries a large share: its stalls pull the pooled p99 up into the stall
  region. Stalls on a low-traffic endpoint are sometimes missed (2 of 30 on the test banks).
