# Known limitations

- **A simulated production service.** Real traffic has correlated failures, client retries that hide errors, and
  regressions that depend on data or feature flags. The comparison between policies should carry over; the
  absolute rates will not.
- **No deployment backend.** The pipeline decides traffic shares and rollbacks but does not drive a load balancer
  or a service mesh. Connecting it means mapping each step to a weighted route (for example an Istio
  VirtualService or an Argo Rollouts step) and reading metrics from Prometheus instead of the simulator.
- **Tail stalls on low-traffic endpoints.** The tail test needs enough slow requests to be sure. On the quietest
  endpoint, stalls in 4% of requests are sometimes missed (2 of 30 tail regressions on the test banks). It also
  loses power once the canary carries a large share of traffic (ADR 1).
- **Ninety minutes to full rollout.** That is slow for a team that ships many times an hour. Shorter bakes trade
  catches for speed; the evaluation can measure any plan.
- **Errors and latency only.** Business metrics (conversion, checkout completion) and resource use (memory,
  CPU) are not compared.
- **Synthetic repositories.** Test selection is measured on generated dependency graphs with 6% undeclared
  dependencies; a real monorepo's share is unknown until measured. The nightly full run is the safety net.
- **In-memory history.** Deploys live in the process; a real deployment would store them, with an audit trail of
  approvals and overrides.
- **No authentication** on the app or the deploy API.
