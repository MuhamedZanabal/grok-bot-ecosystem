"""Measure one research discovery pass (register, fetch via egress proxy, ingest, get,
claims, transitions) and the report build, all through the ControlPlane, local fixture only.
Usage: python3 bench/bench_research.py [N]
"""
import os
import statistics
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
from tests.research_helpers import ResearchHarness, fixture  # noqa: E402


def main(n: int) -> None:
    fx = fixture()
    r = ResearchHarness(fx)
    try:
        bid = r.run.open_branch("bench", "p50_ms", "max", 0.0)  # values rise each pass: every pass is superior
        passes, reports = [], []
        for i in range(n):
            t0 = time.perf_counter()
            out = r.run.discovery_pass(bid, [r.url(f"/bench/{i}")])
            passes.append((time.perf_counter() - t0) * 1000)
            assert out.get("superior") is True, out
            t0 = time.perf_counter()
            r.run.report(bid)
            reports.append((time.perf_counter() - t0) * 1000)
        lat = sorted(passes)
        print(f"BENCH research_pass n={n} p50_ms={statistics.median(lat):.3f} "
              f"p95_ms={lat[max(0, int(0.95 * n) - 1)]:.3f} sources_per_pass=1")
        print(f"BENCH research_report n={n} p50_ms={statistics.median(reports):.3f} "
              f"final_claims={len(r.ledger.claims(bid))}")
    finally:
        r.close()
        fx.close()


if __name__ == "__main__":
    main(min(200, int(sys.argv[1]) if len(sys.argv) > 1 else 20))
