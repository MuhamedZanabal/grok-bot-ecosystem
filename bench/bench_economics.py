"""Measure ControlPlane.execute overhead of the economics ledger (reserve + record + 2 audit
anchors) vs no ledger, and guard cost as same-day rows grow (the guard scans the day's rows).
Usage: python3 bench/bench_economics.py [N]
"""
import os
import statistics
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
from tests.economics_helpers import attach  # noqa: E402
from tests.helpers import Harness  # noqa: E402
from economics import Caps  # noqa: E402


def run(n: int, econ: bool, prefix: str) -> list[float]:
    h = Harness(budget=None)
    from control_plane import Budget, LoopGovernor
    h.cp.governor = LoopGovernor(Budget(10**7, 1e9, 1e9), h.kill, clock=h.mono)
    h.policy.grant("agent.alpha", "cap.fs.read")
    if econ:
        attach(h, Caps(day_usd=1e9, day_calls=10**9), per_call={"cap.fs.read": 0.001})
    lat = []
    try:
        for i in range(n):
            t0 = time.perf_counter()
            out = h.cp.execute(h.req("cap.fs.read", op=f"{prefix}-{i:08d}"))
            lat.append((time.perf_counter() - t0) * 1000)
            assert out.status.value == "executed", out
    finally:
        h.close()
    return lat


def main(n: int) -> None:
    base, led = run(n, False, "op-base"), run(n, True, "op-econ")
    p50 = lambda xs: statistics.median(xs)
    print(f"BENCH econ_execute n={n} p50_ms_no_ledger={p50(base):.3f} p50_ms_ledger={p50(led):.3f} "
          f"overhead_p50_ms={p50(led) - p50(base):.3f}")
    print(f"BENCH econ_guard_growth first50_p50_ms={p50(led[:50]):.3f} last50_p50_ms={p50(led[-50:]):.3f} "
          f"rows_at_end={2 * n} (reservation + operation per op)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
