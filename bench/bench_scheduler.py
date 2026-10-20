"""Measure scheduler tick cost through the ControlPlane: N slots of a 1-step interval job,
one tick per slot (claim + governor + execute + finish), injected clocks.
Usage: python3 bench/bench_scheduler.py [N]
"""
import os
import statistics
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
from tests.scheduler_helpers import SchedHarness  # noqa: E402


def main(n: int) -> None:
    x = SchedHarness()
    try:
        x.at(1_000_020)
        x.define("job.bench")
        x.define("job.idle", every=86400)
        lat, idle = [], []
        for i in range(n):
            x.at(1_000_020 + 60 * (i + 1))
            t0 = time.perf_counter()
            res = x.s.tick()
            lat.append((time.perf_counter() - t0) * 1000)
            assert [r[2] for r in res["ran"]] == ["succeeded"], res
            t0 = time.perf_counter()
            x.s.tick()  # nothing due
            idle.append((time.perf_counter() - t0) * 1000)
        lat.sort()
        print(f"BENCH scheduler_tick_run n={n} p50_ms={statistics.median(lat):.3f} "
              f"p95_ms={lat[max(0, int(0.95 * n) - 1)]:.3f} jobs=2")
        print(f"BENCH scheduler_tick_idle n={n} p50_ms={statistics.median(idle):.3f} max_begins={x.max_begins_per_op()}")
    finally:
        x.close()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
