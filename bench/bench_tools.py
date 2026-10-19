"""Measure ControlPlane.execute through an out-of-process SubprocessHandler (echo tool).

Includes a fresh `python -I -S` worker per call. Compare with bench_control_plane.py
(in-process handler) to get the isolation overhead.
Usage: python3 bench/bench_tools.py [N]
"""
import os
import statistics
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(ROOT, "src")]
from control_plane import (ApprovalAuthority, AuditLog, Budget, Capability, ControlPlane,  # noqa: E402
                           GrantStore, IdempotencyLedger, KillSwitch, LoopGovernor, PolicyEngine, RiskLevel)
from tools import SubprocessHandler, WorkerSpec  # noqa: E402

TOOL_ROOT = os.path.join(ROOT, "tests", "fixtures", "tool_root")


def main(n: int) -> None:
    with tempfile.TemporaryDirectory() as d:
        audit = AuditLog(os.path.join(d, "a.jsonl"))
        pol = PolicyEngine(GrantStore(os.path.join(d, "g.sqlite")), audit)
        pol.register(Capability("cap.bench.echo", RiskLevel.READ, "", {"type": "object", "properties": {"i": {"type": "integer"}}}))
        pol.grant("bench", "cap.bench.echo")
        kill = KillSwitch(os.path.join(d, "KILL"))
        cp = ControlPlane(policy=pol, approvals=ApprovalAuthority(os.urandom(32)),
                          ledger=IdempotencyLedger(os.path.join(d, "l.sqlite")), audit=audit,
                          kill_switch=kill, governor=LoopGovernor(Budget(n + 1, 3600, 0), kill))
        cp.bind("cap.bench.echo", SubprocessHandler(WorkerSpec(TOOL_ROOT, "sample_tools", "echo")))
        lat = []
        for i in range(n):
            t0 = time.perf_counter()
            out = cp.execute({"operation_id": f"op-tool-{i:08d}", "actor": "bench",
                              "capability": "cap.bench.echo", "args": {"i": i}})
            lat.append((time.perf_counter() - t0) * 1000)
            assert out.status.value == "executed", out
        lat.sort()
        print(f"BENCH tool_execute_subprocess n={n} p50_ms={statistics.median(lat):.3f} "
              f"p95_ms={lat[int(0.95 * n) - 1]:.3f} max_ms={lat[-1]:.3f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 50)
