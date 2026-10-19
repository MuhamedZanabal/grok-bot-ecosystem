# Benchmark log (append-only)

## B-0001: Control-plane overhead baseline
- **decision_id:** B-0001
- **date:** 2026-10-19
- **problem:** Measure the per-call cost of the gate so later cycles can detect regressions.
- **options:** no benchmark, or a micro-benchmark of `execute()` with real SQLite and fsync'd audit.
- **evidence:** `python3 bench/bench_control_plane.py 200` on the box (Linux 6.12, 8 vCPU, Python 3.13.5):
  `BENCH execute n=200 p50_ms=0.741 p95_ms=1.707 max_ms=4.462`
  `BENCH replay n=200 p50_ms=0.119 p95_ms=0.488 max_ms=1.334`
  Prior baseline: none (empty repo), so there is no delta. This is a first measurement, not an improvement claim.
- **selected:** micro-benchmark, run informationally in CI (not gating).
- **rejected:** gating CI on latency, because shared runners make timings noisy.
- **tradeoffs:** single machine, small n; no load or concurrency benchmark.
- **reversal criteria:** p50 execute overhead > 10 ms on comparable hardware, or the ledger becomes a bottleneck in Cycle 7 workloads.

## B-0002: CI runner overhead measurement
- **decision_id:** B-0002
- **date:** 2026-10-19
- **problem:** Record the same benchmark on GitHub-hosted runners for cross-environment comparison.
- **options:** n/a (observation).
- **evidence:** CI run 39319720689, job `test (3.13)`: `BENCH execute n=200 p50_ms=1.591 p95_ms=2.097 max_ms=3.644`; `BENCH replay n=200 p50_ms=0.330 p95_ms=0.437 max_ms=2.467`.
- **selected:** informational only; no delta (first measurement).
- **rejected:** gating on these numbers.
- **tradeoffs:** shared runners are noisy.
- **reversal criteria:** as B-0001.

## B-0003: Cycle 2 overhead (local box)
- **decision_id:** B-0003
- **date:** 2026-10-19
- **problem:** Check that the persistent grant lookup did not regress `execute()`, and baseline agent-run overhead.
- **options:** n/a (measurement).
- **evidence:** local box (same as B-0001):
  - `python3 bench/bench_control_plane.py 200` gave `BENCH execute n=200 p50_ms=0.751 p95_ms=1.152 max_ms=3.175` and `BENCH replay n=200 p50_ms=0.118 p95_ms=0.306`. Against B-0001 (execute p50 0.741 ms, replay p50 0.119 ms), the delta is about +0.01 ms on execute p50, within run-to-run noise. Not a measured improvement or regression.
  - `python3 bench/bench_orchestrator.py 100` gave `BENCH agent_run n=100 p50_ms=1.850 p95_ms=2.642 max_ms=5.761`. This is a first measurement (1 action + final, mock provider, so model latency is excluded).
- **selected:** informational, also run in CI.
- **rejected:** gating on latency.
- **tradeoffs:** single run, small n, no real model latency.
- **reversal criteria:** agent-run p50 above 20 ms (overhead only), or execute p50 above 10 ms.

## B-0004: Cycle 2 overhead (CI runner)
- **decision_id:** B-0004
- **date:** 2026-10-19
- **problem:** Cross-environment record.
- **options:** n/a (observation).
- **evidence:** CI run 39321439337, job `test (3.13)`: `BENCH execute n=200 p50_ms=1.375 p95_ms=1.666`; `BENCH replay n=200 p50_ms=0.369`; `BENCH agent_run n=100 p50_ms=3.335 p95_ms=3.681 max_ms=4.021`. Against B-0002 (CI execute p50 1.591 ms) this is a different runner instance, so it is not a comparable delta.
- **selected:** informational.
- **rejected:** gating.
- **tradeoffs:** shared-runner noise.
- **reversal criteria:** as B-0003.

## B-0005: Out-of-process tool call overhead
- **decision_id:** B-0005
- **date:** 2026-10-19
- **problem:** Quantify the cost of isolation (D-0008 left this UNKNOWN).
- **options:** n/a (measurement).
- **evidence:** local box:
  - `python3 bench/bench_tools.py 50` gave `BENCH tool_execute_subprocess n=50 p50_ms=24.810 p95_ms=27.606 max_ms=27.991`.
  - The same session's in-process `python3 bench/bench_control_plane.py 200` gave `execute p50_ms=0.719`, so isolation adds about 24 ms p50 per tool call (one fresh `python -I -S` per call).
  - `bench_orchestrator.py 100` gave `agent_run p50_ms=1.954` (against B-0003's 1.850; noise level). Its `max_ms=192.331` is a single outlier, cause UNKNOWN.
- **selected:** accept the 24 ms. It is below D-0008's 50 ms reversal threshold.
- **rejected:** pooling now.
- **tradeoffs:** reliability and security over latency.
- **reversal criteria:** p50 > 50 ms, or tool-heavy workloads where spawn time dominates.
