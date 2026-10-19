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
