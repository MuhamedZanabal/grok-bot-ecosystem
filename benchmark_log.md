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

## B-0006: Tool call overhead on CI
- **decision_id:** B-0006
- **date:** 2026-10-19
- **problem:** Cross-environment record for B-0005.
- **options:** n/a (observation).
- **evidence:** run 39323831631 `test (3.13)` gave `BENCH tool_execute_subprocess n=50 p50_ms=26.243 p95_ms=45.897`; run 39325010443 `test (3.13)` gave `p50_ms=32.387 p95_ms=33.309`. Both are below D-0008's 50 ms reversal threshold. Run-to-run spread on shared runners is about 6 ms p50.
- **selected:** informational.
- **rejected:** gating.
- **tradeoffs:** noisy runners.
- **reversal criteria:** as B-0005.

## B-0007: Sandbox overhead per tool call
- **decision_id:** B-0007
- **date:** 2026-10-20
- **problem:** Measure the latency cost of bwrap (D-0012 left it UNKNOWN).
- **options:** n/a (measurement).
- **evidence:** local box, same session, `python3 bench/bench_tools.py 50`:
  - sandboxed, three runs: `p50_ms=26.066 / 25.170 / 25.509` (p95 29.353 / 27.641 / 28.309);
  - Cycle 3 unsandboxed code via `git stash`: `p50_ms=23.367 p95_ms=27.642`.
  - The delta is about +2 ms p50 (about 9%). Against B-0005 (24.810) it is +0.7 ms, with cross-session noise of the same order.
  - In-process `bench_control_plane.py 200` gave `execute p50_ms=0.733` (unchanged scale).
  - CI numbers are recorded with the PR run.
- **selected:** accept. It stays far below the 50 ms reversal threshold (B-0005).
- **rejected:** worker pooling (it would weaken per-call isolation).
- **tradeoffs:** none material.
- **reversal criteria:** p50 > 50 ms.

## B-0008: Sandbox overhead on CI
- **decision_id:** B-0008
- **date:** 2026-10-20
- **problem:** Cross-environment record for B-0007.
- **options:** n/a (observation).
- **evidence:** run 39329642600 `test (3.13)` gave `BENCH tool_execute_subprocess n=50 p50_ms=36.781 p95_ms=40.358 max_ms=54.282`, against B-0006 (unsandboxed) `p50 26.243 / 32.387`, so about +4 to +10 ms on a different runner instance (noise is about 6 ms). The same job gave `execute p50_ms=1.667`; `agent_run p50_ms=3.661`.
- **selected:** informational. It stays below the 50 ms p50 threshold, but with less headroom on CI.
- **rejected:** gating.
- **tradeoffs:** shared-runner noise.
- **reversal criteria:** CI p50 > 50 ms on two consecutive runs → investigate the spawn cost (e.g. a pre-built bwrap argv or a lighter probe).

## B-0009: Browser fetch via the egress proxy
- **decision_id:** B-0009
- **date:** 2026-10-20
- **problem:** Measure the cost of the proxy plus HTTP plus extraction.
- **options:** n/a (measurement).
- **evidence:** local box:
  - `python3 bench/bench_browser.py 30` gave `p50_ms=40.942 p95_ms=43.292 max_ms=44.885` after the wake-pipe fix. The first version gave `p50_ms=136.731` (D-0017).
  - Same session, `bench_tools.py 50` (sandboxed echo, no egress) gave `p50_ms=29.035`.
  - So the proxy, `/etc/ssl` mount, HTTP round trip to the local fixture and parsing add about 12 ms p50.
  - System Chrome headless inside bwrap took about 2 s per launch (D-0015 probe; not benchmarked repeatedly).
- **selected:** informational.
- **rejected:** gating.
- **tradeoffs:** the local fixture only; real-site latency is UNKNOWN.
- **reversal criteria:** browser p50 > 200 ms locally.

## B-0010: Browser fetch on CI
- **decision_id:** B-0010
- **date:** 2026-10-20
- **problem:** Cross-environment record for B-0009.
- **options:** n/a (observation).
- **evidence:** run 39338745621 `test (3.13)` gave `BENCH browser_fetch n=30 p50_ms=61.391 p95_ms=79.018` and `tool_execute_subprocess p50_ms=38.988`, so the proxy plus fetch add about 22 ms p50 on the runner. Also `execute p50_ms=1.736`, `agent_run p50_ms=3.595`.
- **selected:** informational.
- **rejected:** gating.
- **tradeoffs:** runner noise.
- **reversal criteria:** as B-0009.

## B-0011: Memory write and search through the control plane
- **decision_id:** B-0011
- **date:** 2026-10-20
- **problem:** Measure the cost of the memory capabilities.
- **options:** n/a (measurement).
- **evidence:** local box:
  - `python3 bench/bench_memory.py 200` gave `BENCH memory_write n=200 p50_ms=2.225 p95_ms=4.443 mode=fts5` and `BENCH memory_search n=200 p50_ms=1.346 p95_ms=2.657`.
  - The same session's `bench_control_plane.py 200` gave `execute p50_ms=0.672`, so a write (redaction, HMAC, SQLite insert with FTS, audit anchor) adds about 1.5 ms and a search about 0.7 ms.
- **selected:** informational.
- **rejected:** gating.
- **tradeoffs:** a 200-record store only; scaling to the 10k-record budget is UNKNOWN (not measured).
- **reversal criteria:** search p50 > 50 ms at budget size.

## B-0012: Memory bench on the CI runner
- **decision_id:** B-0012
- **date:** 2026-10-20
- **problem:** Record the CI-runner memory latency next to B-0011 (local).
- **options:** n/a (measurement).
- **evidence:** run 39342990292, job test (3.13):
  - `BENCH memory_write n=200 p50_ms=3.350 p95_ms=4.434 mode=fts5`
  - `BENCH memory_search n=200 p50_ms=2.066 p95_ms=2.865`
  - Same job: `BENCH execute n=200 p50_ms=1.781`, so a write adds about 1.6 ms and a search about 0.3 ms over a bare execute.
- **selected:** informational.
- **rejected:** gating.
- **tradeoffs:** single run; runner noise; 200-record store only.
- **reversal criteria:** as B-0011.

## B-0013: Research pass and report cost
- **decision_id:** B-0013
- **date:** 2026-10-20
- **problem:** Measure one research discovery pass through the control plane.
- **options:** n/a (measurement).
- **evidence:** local box, `python3 bench/bench_research.py 50`:
  - `BENCH research_pass n=50 p50_ms=58.979 p95_ms=63.684 sources_per_pass=1`
  - `BENCH research_report n=50 p50_ms=2.257 final_claims=50`

  A single-source pass is register, fetch (sandboxed worker via the egress proxy), ingest, get, claim and 2 transitions. It is dominated by the fetch (B-0009 `browser_fetch` p50 ≈ 58-61 ms).
- **selected:** informational. CI runs the same bench.
- **rejected:** gating.
- **tradeoffs:** report cost grows with claims × peers (contradiction check is O(n²) per subject/attribute); untested beyond 50 claims (UNKNOWN).
- **reversal criteria:** report p50 > 1 s at 1k claims.
