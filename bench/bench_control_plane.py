"""Measure ControlPlane.execute overhead (L0 executes and replays) on this machine.

Includes SQLite (synchronous=FULL) ledger writes and an fsync'd audit append per decision.
Usage: python3 bench/bench_control_plane.py [N]
"""
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from control_plane import (ApprovalAuthority, AuditLog, Budget, Capability, ControlPlane,  # noqa: E402
                           IdempotencyLedger, KillSwitch, LoopGovernor, PolicyEngine, RiskLevel)


def main(n: int) -> None:
    with tempfile.TemporaryDirectory() as d:
        pol = PolicyEngine()
        pol.register(Capability("cap.bench.noop", RiskLevel.READ, "", {"type": "object", "properties": {"i": {"type": "integer"}}}))
        pol.grant("bench", "cap.bench.noop")
        kill = KillSwitch(os.path.join(d, "KILL"))
        cp = ControlPlane(policy=pol, approvals=ApprovalAuthority(os.urandom(32)),
                          ledger=IdempotencyLedger(os.path.join(d, "l.sqlite")),
                          audit=AuditLog(os.path.join(d, "a.jsonl")), kill_switch=kill,
                          governor=LoopGovernor(Budget(n + 1, 3600, 0), kill),
                          allow_inprocess_handlers=True)
        cp.bind("cap.bench.noop", lambda a: {"ok": True})
        for label, opid in (("execute", lambda i: f"op-bench-{i:08d}"), ("replay", lambda i: f"op-bench-{i:08d}")):
            lat = []
            for i in range(n):
                t0 = time.perf_counter()
                cp.execute({"operation_id": opid(i), "actor": "bench", "capability": "cap.bench.noop", "args": {"i": i}})
                lat.append((time.perf_counter() - t0) * 1000)
            lat.sort()
            print(f"BENCH {label} n={n} p50_ms={statistics.median(lat):.3f} "
                  f"p95_ms={lat[int(0.95 * n) - 1]:.3f} max_ms={lat[-1]:.3f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
