# Autonomous software delivery: evaluation

200 deploys per bank, about half of them bad. Canary settings were tuned on the tuning worlds only; the held-out worlds and the held-back regression kinds were run once, after the settings were frozen.

## Held-out worlds and held-back kinds together

| Policy | Bad versions caught | Caught at 5% of traffic or less | Requests harmed | Healthy versions rolled back | Minutes to full rollout |
|---|---:|---:|---:|---:|---:|
| Big-bang deploy + service alert | 127 of 192 (66.1%) | 0 | 344,625 | 22 of 208 | 0 |
| Canary vs pre-deploy thresholds | 154 of 192 (80.2%) | 141 | 74,371 | 52 of 208 | 60 |
| Progressive delivery (this platform) | 188 of 192 (97.9%) | 105 | 16,189 | 1 of 208 | 90 |

## By bank

| Bank | Policy | Caught | Harm | False rollbacks |
|---|---|---:|---:|---:|
| Tuning worlds | Big-bang deploy + service alert | 69.0% | 330,776 | 10 |
| Tuning worlds | Canary vs pre-deploy thresholds | 80.0% | 108,906 | 21 |
| Tuning worlds | Progressive delivery (this platform) | 99.0% | 9,876 | 0 |
| Held-out worlds | Big-bang deploy + service alert | 69.2% | 262,110 | 6 |
| Held-out worlds | Canary vs pre-deploy thresholds | 85.6% | 60,178 | 21 |
| Held-out worlds | Progressive delivery (this platform) | 98.1% | 12,834 | 1 |
| Held-back regression kinds | Big-bang deploy + service alert | 62.5% | 82,515 | 16 |
| Held-back regression kinds | Canary vs pre-deploy thresholds | 73.9% | 14,193 | 31 |
| Held-back regression kinds | Progressive delivery (this platform) | 97.7% | 3,355 | 0 |

## Bad versions caught, by kind (held-out and held-back)

| Regression | Big-bang deploy + service alert | Canary vs pre-deploy thresholds | Progressive delivery (this platform) |
|---|---:|---:|---:|
| One endpoint slower | 4 of 13 | 7 of 13 | 13 of 13 |
| Errors everywhere | 21 of 25 | 25 of 25 | 25 of 25 |
| Errors growing (leak) | 13 of 13 | 13 of 13 | 13 of 13 |
| Errors in one region | 14 of 23 | 17 of 23 | 23 of 23 |
| Stalls on one endpoint | 20 of 30 | 27 of 30 | 28 of 30 |
| Errors in bursts | 40 of 44 | 44 of 44 | 44 of 44 |
| Errors in one cell | 15 of 44 | 21 of 44 | 42 of 44 |

## Test selection (3 repositories, 6,000 changes, 1,659 bugs a full run catches)

| Strategy | Tests run | Bugs caught | Waiting for the nightly run | Red without a bug (retry known flaky / no retry) |
|---|---:|---:|---:|---:|
| Every test | 100.0% | 100.0% | 0 | 19.9% / 37.5% |
| Tests in the changed directories | 8.0% | 71.0% | 481 | 1.8% / 4.1% |
| Impact analysis (this platform) | 37.5% | 99.8% | 3 | 7.3% / 14.1% |

## Gate (regression floors, not targets)

- PASS heldout_caught_share_min: 0.9808 (limit 0.95)
- PASS fresh_caught_share_min: 0.9773 (limit 0.93)
- PASS false_rollback_share_max: 0.0048 (limit 0.02)
- PASS harm_vs_big_bang_max: 0.049 (limit 0.1)
- PASS impact_bugs_caught_share_min: 0.9982 (limit 0.99)
