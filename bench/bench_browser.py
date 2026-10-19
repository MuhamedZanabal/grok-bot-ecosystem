"""Measure cap.browser.fetch: sandboxed worker + host egress proxy + local fixture server.
Compare with bench_tools.py (sandboxed echo, no egress) for the proxy + HTTP overhead.
Usage: python3 bench/bench_browser.py [N]
"""
import os
import statistics
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
from control_plane import AuditLog  # noqa: E402
from tests.egress_fixtures import Fixture  # noqa: E402
from tools import SubprocessHandler, browser_fetch_spec  # noqa: E402


def main(n: int) -> None:
    fx = Fixture()
    try:
        with tempfile.TemporaryDirectory() as d:
            h = SubprocessHandler(browser_fetch_spec((fx.entry,)), audit=AuditLog(os.path.join(d, "a.jsonl")),
                                  capability="cap.browser.fetch")
            lat = []
            for _ in range(n):
                t0 = time.perf_counter()
                r = h({"url": fx.base + "/page"})
                lat.append((time.perf_counter() - t0) * 1000)
                assert r["ok"], r
        lat.sort()
        print(f"BENCH browser_fetch n={n} p50_ms={statistics.median(lat):.3f} "
              f"p95_ms={lat[int(0.95 * n) - 1]:.3f} max_ms={lat[-1]:.3f}")
    finally:
        fx.close()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
