import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from control_plane import (ApprovalAuthority, AuditLog, Budget, Capability, ControlPlane,  # noqa: E402
                           GrantStore, IdempotencyLedger, KillSwitch, LoopGovernor, PolicyEngine,
                           RateLimiter, RiskLevel)

OPEN_SCHEMA = {"type": "object", "additionalProperties": True}
CAPS = [("cap.fs.read", RiskLevel.READ), ("cap.fs.write", RiskLevel.LOCAL_WRITE),
        ("cap.email.send", RiskLevel.EXTERNAL), ("cap.payment.transfer", RiskLevel.IRREVERSIBLE)]

KEY = b"k" * 32
FAKE_GH_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Harness:
    """A fully wired control plane in a temp dir, with call counters per capability."""

    def __init__(self, budget: Budget | None = None, timeout: float = 2.0,
                 rate: RateLimiter | None = None, inprocess: bool = True) -> None:
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.wall = Clock()
        self.mono = Clock(0.0)
        self.ledger_path = os.path.join(d, "ledger.sqlite")
        self.audit_path = os.path.join(d, "audit.jsonl")
        self.grants_path = os.path.join(d, "grants.sqlite")
        self.grant_store = GrantStore(self.grants_path)
        self.audit = AuditLog(self.audit_path)
        self.policy = PolicyEngine(self.grant_store, self.audit)
        for cid, risk in CAPS:
            self.policy.register(Capability(cid, risk, f"test capability {cid}", OPEN_SCHEMA))
        self.approvals = ApprovalAuthority(KEY, clock=self.wall)
        self.kill_path = os.path.join(d, "KILL")
        self.ledger = IdempotencyLedger(self.ledger_path)
        self.kill = KillSwitch(self.kill_path)
        self.governor = LoopGovernor(budget or Budget(100, 3600, 100.0), self.kill, clock=self.mono)
        try:
            self.cp = ControlPlane(policy=self.policy, approvals=self.approvals, ledger=self.ledger,
                                   audit=self.audit, kill_switch=self.kill, governor=self.governor,
                                   rate_limiter=rate, handler_timeout_s=timeout,
                                   allow_inprocess_handlers=inprocess)  # legacy in-process test handlers
        except BaseException:
            self.close()
            raise
        self.calls: dict[str, int] = {}
        if inprocess:
            for cid in ("cap.fs.read", "cap.fs.write", "cap.email.send", "cap.payment.transfer"):
                self.cp.bind(cid, self._counter(cid))

    def _counter(self, cid):
        def handler(args):
            self.calls[cid] = self.calls.get(cid, 0) + 1
            return {"cap": cid, "n": self.calls[cid], "echo": args}
        return handler

    def total_calls(self) -> int:
        return sum(self.calls.values())

    def req(self, cap: str, op: str = "op-00000001", args=None, actor: str = "agent.alpha", token=None):
        r = {"operation_id": op, "actor": actor, "capability": cap, "args": args if args is not None else {"x": 1}}
        if token is not None:
            r["approval_token"] = token
        return r

    def approve(self, cap: str, op: str = "op-00000001", args=None, actor: str = "agent.alpha", ttl: int = 300):
        return self.approvals.issue(actor=actor, capability=cap, operation_id=op,
                                    args=args if args is not None else {"x": 1},
                                    ttl_seconds=ttl, approver="zanabal")

    def close(self) -> None:
        self.ledger.close()
        self.grant_store.close()
        self.dir.cleanup()


def act(cap: str, args: dict | None = None, **extra) -> str:
    import json
    return json.dumps({"type": "action", "capability": cap, "args": args if args is not None else {}, **extra})


def final(answer: str = "done") -> str:
    import json
    return json.dumps({"type": "final", "answer": answer})


def make_runner(h: "Harness", *scripts, actor: str = "agent.alpha", **cfg):
    from orchestrator import AgentRunner, MockProvider, RunConfig
    providers = [s if isinstance(s, MockProvider) else MockProvider(s, name=f"mock{i}")
                 for i, s in enumerate(scripts)]
    runner = AgentRunner(h.cp.gateway(actor), providers, h.audit, RunConfig(**cfg), h.kill, clock=h.mono)
    return runner, providers


TOOL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "tool_root")


def worker(function: str, **kw):
    from tools import WorkerSpec
    kw.setdefault("timeout_s", 5.0)
    return WorkerSpec(TOOL_ROOT, "sample_tools", function, **kw)


def host_procs(*argv_prefixes: tuple) -> list[int]:
    """Host-visible PIDs whose argv starts with one of the given tuples (e.g. ("sleep", "3599"))."""
    found = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/cmdline", "rb") as fh:
                argv = tuple(a.decode(errors="replace") for a in fh.read().split(b"\0") if a)
            with open(f"/proc/{d}/stat") as fh:
                if fh.read().rsplit(")", 1)[-1].split()[0] == "Z":
                    continue
        except OSError:
            continue
        if any(argv[:len(p)] == p for p in argv_prefixes):
            found.append(int(d))
    return found


def wait_gone(*argv_prefixes: tuple, timeout: float = 3.0) -> list[int]:
    import time
    deadline = time.monotonic() + timeout
    while True:
        left = host_procs(*argv_prefixes)
        if not left or time.monotonic() > deadline:
            return left
        time.sleep(0.05)
