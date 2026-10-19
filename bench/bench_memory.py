"""Measure cap.memory.write / cap.memory.search through the ControlPlane (trusted host handlers).
Usage: python3 bench/bench_memory.py [N]
"""
import os
import statistics
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
from control_plane import Budget  # noqa: E402
from tests.helpers import Harness  # noqa: E402
from tests.memory_helpers import attach_memory, search, write  # noqa: E402


def pct(lat, q):
    lat = sorted(lat)
    return lat[max(0, int(q * len(lat)) - 1)]


def main(n: int) -> None:
    h = Harness(budget=Budget(2 * n + 10, 3600, 1e9), inprocess=False)
    mem = attach_memory(h)
    try:
        w, s = [], []
        for i in range(n):
            t0 = time.perf_counter()
            out = write(h, f"benchmark record {i} about topic{i % 10} " + "lorem ipsum " * 50, f"op-bench-w-{i:06d}")
            w.append((time.perf_counter() - t0) * 1000)
            assert out.status.value == "executed", out
        for i in range(n):
            t0 = time.perf_counter()
            out = search(h, f"topic{i % 10} benchmark", f"op-bench-s-{i:06d}")
            s.append((time.perf_counter() - t0) * 1000)
            assert out.result["items"], out
        print(f"BENCH memory_write n={n} p50_ms={statistics.median(w):.3f} p95_ms={pct(w, .95):.3f} "
              f"mode={mem.search_mode}")
        print(f"BENCH memory_search n={n} p50_ms={statistics.median(s):.3f} p95_ms={pct(s, .95):.3f}")
    finally:
        mem.close()
        h.close()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
