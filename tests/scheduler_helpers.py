"""Wiring for cycle-008 scheduler tests: injectable wall + monotonic clocks, in-process
test capabilities with controllable faults, exactly-once evidence from the audit log."""
import json
import os
from collections import Counter

from tests.helpers import Harness, OPEN_SCHEMA  # noqa: I001 (puts src/ on sys.path first)
from control_plane import Budget, Capability, RiskLevel
from scheduler import JobStore, Scheduler

SCHED_KEY = b"s" * 32
ACTOR = "agent.jobs"


class Crash(BaseException):
    """Simulated process death inside a handler: escapes the control plane like a real crash."""


class SchedHarness:
    def __init__(self, **sched_kw) -> None:
        self.h = Harness(budget=Budget(1_000_000, 1e9, 1e9), inprocess=True)
        self.calls: Counter = Counter()
        self.crash_next = 0  # raise Crash on the next N calls of cap.job.crash
        self.kill_in_step = False
        self.advance_mono = 0.0
        for cid in ("cap.job.step", "cap.job.crash", "cap.job.kill", "cap.job.slow", "cap.scheduler.admin"):
            self.h.policy.register(Capability(cid, RiskLevel.LOCAL_WRITE, f"test {cid}", OPEN_SCHEMA))
            self.h.cp.bind(cid, self._handler(cid))
        for cid in ("cap.job.step", "cap.job.crash", "cap.job.kill", "cap.job.slow", "cap.scheduler.admin",
                    "cap.fs.read", "cap.fs.write", "cap.email.send"):
            self.h.policy.grant(ACTOR, cid)
        self.path = os.path.join(self.h.dir.name, "jobs.sqlite")
        self.stores = []
        self.sched_kw = sched_kw
        self.s = self.new()

    def _handler(self, cid):
        def handler(args):
            self.calls[cid] += 1
            if cid == "cap.job.crash" and self.crash_next > 0:
                self.crash_next -= 1
                raise Crash()
            if cid == "cap.job.kill" and self.kill_in_step:
                self.h.kill.engage("operator stop mid-run")
            if cid == "cap.job.slow":
                self.h.mono.t += self.advance_mono
            return {"ok": True, "cap": cid}
        return handler

    def new(self, owner="sched-1", key=SCHED_KEY, **kw) -> Scheduler:
        """A fresh scheduler instance (a 'restart') over the same persistent store."""
        store = JobStore(self.path, key)
        self.stores.append(store)
        opts = dict(self.sched_kw, **kw)
        return Scheduler(self.h.cp, store, self.h.audit, self.h.kill, clock=self.h.wall, mono=self.h.mono,
                         owner=owner, **opts)

    def define(self, job_id, steps=None, every=60, **kw):
        steps = steps if steps is not None else [{"capability": "cap.job.step", "args": {"job": job_id}}]
        return self.s.define(job_id, ACTOR, steps, {"kind": "interval", "every_s": every, "anchor": 0}, **kw)

    def at(self, t):
        self.h.wall.t = float(t)

    def events(self, name):
        with open(self.h.audit_path) as fh:
            return [json.loads(line)["data"] for line in fh if f'"event":"{name}"' in line]

    def max_begins_per_op(self) -> int:
        c = Counter(e["operation_id"] for e in self.events("execute_begin"))
        return max(c.values()) if c else 0

    def close(self):
        for st in self.stores:
            st.close()
        self.h.close()
