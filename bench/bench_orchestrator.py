"""Measure AgentRunner overhead per run (1 L0 action + final answer) with the MockProvider.

Excludes model latency by construction (mock returns instantly); includes provider thread,
parsing, gateway -> ControlPlane.execute, SQLite ledger and fsync'd audit appends.
Usage: python3 bench/bench_orchestrator.py [N]
"""
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from control_plane import (ApprovalAuthority, AuditLog, Budget, Capability, ControlPlane,  # noqa: E402
                           GrantStore, IdempotencyLedger, KillSwitch, LoopGovernor, PolicyEngine, RiskLevel)
from orchestrator import AgentRunner, MockProvider  # noqa: E402


def main(n: int) -> None:
    with tempfile.TemporaryDirectory() as d:
        audit = AuditLog(os.path.join(d, "a.jsonl"))
        pol = PolicyEngine(GrantStore(os.path.join(d, "g.sqlite")), audit)
        pol.register(Capability("cap.bench.noop", RiskLevel.READ, "", {"type": "object", "properties": {"i": {"type": "integer"}}}))
        pol.grant("bench", "cap.bench.noop")
        kill = KillSwitch(os.path.join(d, "KILL"))
        cp = ControlPlane(policy=pol, approvals=ApprovalAuthority(os.urandom(32)),
                          ledger=IdempotencyLedger(os.path.join(d, "l.sqlite")), audit=audit,
                          kill_switch=kill, governor=LoopGovernor(Budget(n + 1, 3600, 0), kill),
                          allow_inprocess_handlers=True)
        cp.bind("cap.bench.noop", lambda a: {"ok": True})
        gw = cp.gateway("bench")
        lat = []
        for i in range(n):
            script = [json.dumps({"type": "action", "capability": "cap.bench.noop", "args": {"i": i}}),
                      json.dumps({"type": "final", "answer": "ok"})]
            runner = AgentRunner(gw, [MockProvider(script)], audit, kill_switch=kill)
            t0 = time.perf_counter()
            res = runner.run("bench", f"run-{i}")
            lat.append((time.perf_counter() - t0) * 1000)
            assert res.state.value == "completed", res
        lat.sort()
        print(f"BENCH agent_run n={n} p50_ms={statistics.median(lat):.3f} "
              f"p95_ms={lat[int(0.95 * n) - 1]:.3f} max_ms={lat[-1]:.3f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
