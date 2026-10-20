"""Workflow scheduler (cycle-008, D-0022).

* Jobs are defined only through the host operator API `define` (never by an agent or a job).
  Refused: ungranted steps, steps above LOCAL_WRITE (no unattended approvals), steps that
  target cap.scheduler.*, unknown step keys, bad schedules/budgets. Definitions are MAC'd;
  a tampered definition is quarantined, never run. Policy is re-checked by the ControlPlane
  on every step, so a revoked grant stops the job.
* Each run is a bounded LoopGovernor loop; every step is ControlPlane.execute as the job's
  actor with operation_id job:<job_id>:<slot>:<i>, so a step's effect happens at most once
  even across crash/restart (replay or in_doubt, never a second execution).
* tick(): kill switch halts; backwards clock is audited and never re-opens old slots
  (per-job high_water); expired leases are re-driven; missed slots follow the job's
  explicit policy; a global live-lease limit defers (not drops) excess slots.
"""

from dataclasses import asdict, dataclass
import re
import time

from control_plane import Budget, LoopGovernor, LoopState, RiskLevel, Status
from control_plane.canonical import canonical_json

from .schedule import ScheduleError, due, parse

JOB_ID = re.compile(r"[a-z][a-z0-9_.-]{2,40}")
UNATTENDED_MAX_RISK = RiskLevel.LOCAL_WRITE


@dataclass(frozen=True)
class JobBudget:
    max_steps: int = 5
    max_wall_s: float = 60.0
    max_cost: float = 0.0


class DefinitionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Scheduler:
    def __init__(self, cp, store, audit, kill, *, clock=time.time, mono=time.monotonic, owner="sched-1",
                 max_concurrent=2, lease_s=300, backwards_tolerance_s=5) -> None:
        self.cp, self.store, self.audit, self.kill = cp, store, audit, kill
        self.clock, self.mono, self.owner = clock, mono, owner
        self.max_concurrent, self.lease_s, self.tolerance = max_concurrent, lease_s, backwards_tolerance_s

    # ------------------------------------------------------------------ definition (operator)
    def define(self, job_id, actor, steps, schedule, *, missed="skip", catch_up_max=3, budget=JobBudget(),
               by="operator") -> dict:
        def no(code):
            self.audit.append("job_define_refused", job_id=str(job_id)[:64], actor=str(actor)[:64], reason=code, by=by)
            raise DefinitionError(code)
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            no("bad_job_id")
        try:
            parse(schedule)
        except ScheduleError as exc:
            no(f"schedule:{exc}")
        if missed not in ("skip", "catch_up") or not (isinstance(catch_up_max, int) and 1 <= catch_up_max <= 100):
            no("bad_missed_policy")
        if not isinstance(steps, list) or not 1 <= len(steps) <= budget.max_steps:
            no("steps_exceed_budget")
        Budget(budget.max_steps, budget.max_wall_s, budget.max_cost)  # validates numbers
        for st in steps:
            if not isinstance(st, dict) or not {"capability", "args"} <= set(st) <= {"capability", "args", "cost"}:
                no("bad_step_keys")
            cap = st["capability"]
            if not isinstance(cap, str) or cap.startswith("cap.scheduler."):
                no("scheduler_capability_not_schedulable")
            registered = self.cp.policy.capability(cap)
            if registered is None:
                no("unknown_capability")
            if registered.risk > UNATTENDED_MAX_RISK:
                no("risk_exceeds_unattended_ceiling")
            if not self.cp.policy.evaluate(actor, cap).allowed:
                no("actor_not_granted")
            try:
                canonical_json(st["args"])
            except Exception:
                no("bad_args")
        spec = {"actor": actor, "steps": steps, "schedule": {k: v for k, v in schedule.items()}, "missed": missed,
                "catch_up_max": catch_up_max, "budget": asdict(budget), "defined_by": by}
        if not self.store.put_job(job_id, spec, int(self.clock())):
            no("job_exists")
        self.audit.append("job_defined", job_id=job_id, actor=actor, by=by, steps=[s["capability"] for s in steps],
                          schedule=spec["schedule"], missed=missed)
        return spec

    # ------------------------------------------------------------------ one run
    def _drive(self, job_id, spec, slot) -> str:
        b = spec["budget"]
        gov = LoopGovernor(Budget(b["max_steps"], b["max_wall_s"], b["max_cost"]), self.kill, clock=self.mono)
        state, reason = "succeeded", ""
        for i, st in enumerate(spec["steps"]):
            cost = st.get("cost", 0.0)
            ls = gov.admit(cost)
            if ls is not LoopState.RUNNING:
                state, reason = ("killed" if ls is LoopState.KILLED else "budget_exhausted"), gov.reason
                break
            out = self.cp.execute({"operation_id": f"job:{job_id}:{slot}:{i}", "actor": spec["actor"],
                                   "capability": st["capability"], "args": st["args"]})
            if out.status in (Status.EXECUTED, Status.REPLAYED):
                self.store.step_done(job_id, slot, i + 1)
                continue
            state = {Status.IN_DOUBT: "in_doubt", Status.KILLED: "killed"}.get(out.status, "failed")
            reason = f"step{i}:{out.status.value}:{out.reason}"
            break
        self.store.finish(job_id, slot, self.owner, state, reason, int(self.clock()))
        self.audit.append("job_run_finished", job_id=job_id, slot=slot, state=state, reason=reason, owner=self.owner)
        return state

    # ------------------------------------------------------------------ tick
    def tick(self) -> dict:
        res = {"halted": False, "ran": [], "skipped": 0, "deferred": 0, "recovered": [], "quarantined": []}
        if self.kill.engaged():
            self.audit.append("scheduler_halted", reason="kill_switch_engaged", owner=self.owner)
            return res | {"halted": True}
        now = int(self.clock())
        last = self.store.observe_wall(now)
        if last is not None and now < last - self.tolerance:
            self.audit.append("clock_backwards", now=now, last=last, owner=self.owner)
            res["clock_backwards"] = True
        jobs = {j: (spec, hw) for j, spec, hw in self.store.jobs()}
        for r in self.store.expired(now):  # crashed or abandoned runs: re-drive with the same op ids
            spec = jobs.get(r["job_id"], (None, 0))[0]
            if self.store.take_over(r["job_id"], r["slot"], r["lease_until"], self.owner, now + self.lease_s):
                self.audit.append("run_recovered", job_id=r["job_id"], slot=r["slot"], from_owner=r["owner"])
                if spec is None:
                    self.store.finish(r["job_id"], r["slot"], self.owner, "quarantined", "job_mac_mismatch", now)
                    continue
                res["recovered"].append((r["job_id"], r["slot"], self._drive(r["job_id"], spec, r["slot"])))
        for job_id, (spec, hw) in jobs.items():
            if spec is None:
                self.audit.append("job_quarantined", job_id=job_id, reason="job_mac_mismatch")
                res["quarantined"].append(job_id)
                continue
            limit = 1 if spec["missed"] == "skip" else spec["catch_up_max"]
            slots, total = due(parse(spec["schedule"]), hw, now, limit)
            if not slots:
                continue
            if total == -1 or total > len(slots):
                skipped = -1 if total == -1 else total - len(slots)
                if self.store.gap(job_id, hw, slots[0] - 1, skipped, spec["missed"]):
                    self.audit.append("slots_skipped", job_id=job_id, after=hw, upto=slots[0] - 1,
                                      skipped=skipped, policy=spec["missed"])
                    res["skipped"] += max(skipped, 0)
                self.store.advance(job_id, slots[0] - 1)
            for slot in slots:
                if self.store.live(now) >= self.max_concurrent:
                    res["deferred"] += 1  # not claimed, high_water not advanced: picked up later
                    self.audit.append("slot_deferred", job_id=job_id, slot=slot, reason="concurrency_limit")
                    break
                if self.store.live(now, job_id):
                    if self.store.claim(job_id, slot, self.owner, now, None, "skipped_overlap", "previous_run_live"):
                        self.audit.append("slot_skipped_overlap", job_id=job_id, slot=slot)
                    self.store.advance(job_id, slot)
                    continue
                if not self.store.claim(job_id, slot, self.owner, now, now + self.lease_s):
                    self.audit.append("slot_already_claimed", job_id=job_id, slot=slot, owner=self.owner)
                    self.store.advance(job_id, slot)
                    continue
                self.store.advance(job_id, slot)
                self.audit.append("job_run_started", job_id=job_id, slot=slot, owner=self.owner)
                res["ran"].append((job_id, slot, self._drive(job_id, spec, slot)))
        return res
