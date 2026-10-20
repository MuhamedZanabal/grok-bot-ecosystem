"""cycle-008 scheduler adversarial set (D-0022 metric 4). Every case also asserts that no
operation id ever had more than one execute_begin (a step's effect never happens twice)."""
import json
import sqlite3
import sys
import unittest

from tests.scheduler_helpers import ACTOR, Crash, SchedHarness
from scheduler import DefinitionError, TERMINAL

T0 = 1_000_020
LEASE = 300


def mk():
    x = SchedHarness(lease_s=LEASE)
    x.at(T0)
    return x


def expect_refused(x, code, *args, **kw):
    try:
        x.s.define(*args, **kw)
    except DefinitionError as exc:
        return [] if exc.code == code else [f"refused {exc.code}, expected {code}"]
    return [f"define accepted, expected {code}"]


def slot_begins(x, slot):
    """{step index: execute_begin count} for one slot of any job."""
    out = {}
    for e in x.events("execute_begin"):
        _, _, sl, i = e["operation_id"].rsplit(":", 3) if e["operation_id"].count(":") >= 3 else ("", "", "", "")
        if sl == str(slot):
            out[int(i)] = out.get(int(i), 0) + 1
    return out


def duplicate_slot(x):
    x.define("job.dup")
    x.at(T0 + 60)
    s2 = x.new(owner="sched-2")
    r1 = x.s.tick()
    db = sqlite3.connect(x.path)  # simulate a racing instance that read a stale high_water
    db.execute("UPDATE jobs SET high_water=? WHERE job_id='job.dup'", (T0,))
    db.commit()
    db.close()
    r2 = s2.tick()
    v = [] if [r[2] for r in r1["ran"]] == ["succeeded"] and r2["ran"] == [] else [f"{r1} {r2}"]
    v += [] if len(x.events("slot_already_claimed")) == 1 else ["no claim conflict recorded"]
    return v + ([] if x.calls["cap.job.step"] == 1 else [f"calls {x.calls}"])


def restart_storm(x):
    x.define("job.s1")
    x.define("job.s2", steps=[{"capability": "cap.job.step", "args": {"n": 1}},
                              {"capability": "cap.job.crash", "args": {}},
                              {"capability": "cap.fs.write", "args": {"n": 2}}], every=90)
    x.define("job.s3", every=45, missed="catch_up", catch_up_max=2)
    crashes = 0
    for i in range(60):  # 60 restarts, each a fresh instance; every 7th run crashes mid-step
        if i % 7 == 3:
            x.crash_next = 1  # armed until the next crash-step execution
        x.at(T0 + 30 * (i + 1))
        try:
            x.new(owner=f"sched-{i}").tick()
        except Crash:
            crashes += 1
    x.crash_next = 0
    x.at(T0 + 30 * 61 + LEASE + 1)
    x.new(owner="sched-final").tick()
    runs = x.s.store.runs()
    v = [] if crashes >= 5 else [f"only {crashes} crashes"]
    v += [] if all(r["state"] in TERMINAL for r in runs) else ["run left non-terminal"]
    v += [] if {r["state"] for r in runs} >= {"succeeded", "in_doubt"} else [f"states {[r['state'] for r in runs]}"]
    return v


def privilege_escalation_definitions(x):
    v = []
    step = {"capability": "cap.job.step", "args": {}}
    iv = {"kind": "interval", "every_s": 60}
    v += expect_refused(x, "risk_exceeds_unattended_ceiling", "job.mail", ACTOR,
                        [{"capability": "cap.email.send", "args": {}}], iv)
    v += expect_refused(x, "risk_exceeds_unattended_ceiling", "job.pay", ACTOR,
                        [{"capability": "cap.payment.transfer", "args": {}}], iv)
    v += expect_refused(x, "scheduler_capability_not_schedulable", "job.self", ACTOR,
                        [{"capability": "cap.scheduler.admin", "args": {"define": "job.evil", "actor": "root"}}], iv)
    v += expect_refused(x, "bad_step_keys", "job.actor", ACTOR, [dict(step, actor="system.admin")], iv)
    v += expect_refused(x, "bad_step_keys", "job.token", ACTOR, [dict(step, approval_token="x")], iv)
    v += expect_refused(x, "actor_not_granted", "job.other", "agent.other", [step], iv)
    v += expect_refused(x, "unknown_capability", "job.unknown", ACTOR, [{"capability": "cap.root.shell", "args": {}}],
                        iv)
    x.define("job.ok")
    v += expect_refused(x, "job_exists", "job.ok", ACTOR, [{"capability": "cap.fs.write", "args": {}}], iv)
    v += [] if len(x.s.store.jobs()) == 1 else ["refused job stored"]
    return v + ([] if len(x.events("job_define_refused")) == 8 else ["refusals not audited"])


def tampered_job_row(x):
    x.define("job.t")
    db = sqlite3.connect(x.path)
    spec = json.loads(db.execute("SELECT spec FROM jobs").fetchone()[0])
    spec["actor"] = "system.admin"
    spec["steps"] = [{"capability": "cap.payment.transfer", "args": {"amount": 1}}]
    db.execute("UPDATE jobs SET spec=?", (json.dumps(spec),))
    db.commit()
    db.close()
    x.at(T0 + 600)
    res = x.s.tick()
    v = [] if res["quarantined"] == ["job.t"] and res["ran"] == [] else [str(res)]
    return v + ([] if not x.events("execute_begin") else ["tampered job executed"])


def wrong_key_restart(x):
    x.define("job.k")
    x.at(T0 + 60)
    res = x.new(owner="sched-2", key=b"z" * 32).tick()
    return [] if res["quarantined"] == ["job.k"] and not x.events("execute_begin") else [str(res)]


def grant_revoked_after_define(x):
    x.define("job.rev")
    x.h.policy.revoke(ACTOR, "cap.job.step")
    x.at(T0 + 60)
    run = x.s.tick()["ran"]
    v = [] if run and run[0][2] == "failed" else [str(run)]
    return v + ([] if x.calls["cap.job.step"] == 0 else ["revoked capability executed"])


def backwards_clock(x):
    x.define("job.clock")
    x.at(T0 + 120)
    a = x.s.tick()["ran"]
    x.at(T0 - 3600)
    b = x.s.tick()
    x.at(T0 + 130)
    c = x.s.tick()["ran"]
    x.at(T0 + 180)
    d = x.s.tick()["ran"]
    v = [] if [r[1] for r in a] == [T0 + 120] and b["ran"] == [] and b.get("clock_backwards") else [f"{a} {b}"]
    v += [] if c == [] and [r[1] for r in d] == [T0 + 180] else [f"{c} {d}"]
    return v + ([] if len(x.events("clock_backwards")) == 1 else ["backwards clock not audited"])


def kill_switch_mid_run(x):
    x.kill_in_step = True
    x.define("job.kill", steps=[{"capability": "cap.job.kill", "args": {}},
                                {"capability": "cap.job.step", "args": {}}])
    x.at(T0 + 60)
    run = x.s.tick()["ran"]
    x.at(T0 + 120)
    halted = x.s.tick()
    v = [] if run == [("job.kill", T0 + 60, "killed")] and x.calls["cap.job.step"] == 0 else [str(run)]
    v += [] if halted["halted"] and halted["ran"] == [] else [str(halted)]
    x.h.kill.release()
    x.kill_in_step = False
    x.at(T0 + 180)
    after = x.s.tick()["ran"]
    return v + ([] if [r[1] for r in after] == [T0 + 180] else [f"after release {after}"])


def crash_mid_step(x):
    x.define("job.crash", steps=[{"capability": "cap.job.step", "args": {}},
                                 {"capability": "cap.job.crash", "args": {}},
                                 {"capability": "cap.fs.write", "args": {}}])
    x.crash_next = 1
    x.at(T0 + 60)
    v = []
    try:
        x.s.tick()
        v.append("no crash")
    except Crash:
        pass
    x.at(T0 + 61)
    early = x.new(owner="sched-2").tick()  # lease still live: nothing re-driven
    x.at(T0 + 60 + LEASE + 1)
    late = x.new(owner="sched-3").tick()
    v += [] if early["recovered"] == [] and early["ran"] == [] else [f"early {early}"]
    v += [] if late["recovered"] == [("job.crash", T0 + 60, "in_doubt")] else [f"late {late}"]
    return v + ([] if slot_begins(x, T0 + 60) == {0: 1, 1: 1} else [f"begins {slot_begins(x, T0 + 60)}"])


def crash_between_steps(x):
    x.define("job.between", steps=[{"capability": "cap.job.step", "args": {}},
                                   {"capability": "cap.fs.write", "args": {}}])
    store = x.s.store
    real = store.step_done
    state = {"n": 0}

    def dying(*a):
        state["n"] += 1
        if state["n"] == 1:
            raise Crash()
        return real(*a)
    object.__setattr__(store, "step_done", dying)
    x.at(T0 + 60)
    try:
        x.s.tick()
    except Crash:
        pass
    x.at(T0 + 60 + LEASE + 1)
    rec = x.new(owner="sched-2").tick()["recovered"]
    v = [] if rec == [("job.between", T0 + 60, "succeeded")] else [str(rec)]
    return v + ([] if slot_begins(x, T0 + 60) == {0: 1, 1: 1} else [f"begins {slot_begins(x, T0 + 60)}"])


def overlap(x):
    x.define("job.ov")
    x.s.store.claim("job.ov", T0 + 1, "other-instance", T0, T0 + 10_000)  # long-running run elsewhere
    x.at(T0 + 60)
    res = x.s.tick()
    st = {r["slot"]: r["state"] for r in x.s.store.runs("job.ov")}
    v = [] if st.get(T0 + 60) == "skipped_overlap" and res["ran"] == [] else [f"{res} {st}"]
    return v + ([] if x.calls["cap.job.step"] == 0 else ["overlapping run executed"])


def forward_jump(x):
    x.define("job.jump", missed="catch_up", catch_up_max=3)
    x.at(T0 + 365 * 86400)
    res = x.s.tick()
    gaps = x.s.store.gaps("job.jump")
    v = [] if len(res["ran"]) == 3 and gaps and gaps[0]["skipped"] == 365 * 1440 - 3 else [f"{len(res['ran'])} {gaps}"]
    return v + ([] if x.s.tick()["ran"] == [] else ["catch-up repeated"])


def lease_takeover_race(x):
    x.define("job.race", steps=[{"capability": "cap.job.crash", "args": {}}])
    x.crash_next = 1
    x.at(T0 + 60)
    try:
        x.s.tick()
    except Crash:
        pass
    row = x.s.store.runs("job.race")[0]
    a, b = x.new(owner="a").store, x.new(owner="b").store
    first = a.take_over("job.race", T0 + 60, row["lease_until"], "a", T0 + 9999)
    second = b.take_over("job.race", T0 + 60, row["lease_until"], "b", T0 + 9999)
    return [] if (first, second) == (True, False) else [f"take_over {first} {second}"]


CASES = [duplicate_slot, restart_storm, privilege_escalation_definitions, tampered_job_row, wrong_key_restart,
         grant_revoked_after_define, backwards_clock, kill_switch_mid_run, crash_mid_step, crash_between_steps,
         overlap, forward_jump, lease_takeover_race]


class SchedulerAdversarialTests(unittest.TestCase):
    def test_scheduler_adversarial_set(self):
        violations, worst = {}, 0
        for fn in CASES:
            with self.subTest(case=fn.__name__):
                x = mk()
                try:
                    got = fn(x)
                    m = x.max_begins_per_op()
                    worst = max(worst, m)
                    if m > 1:
                        got.append(f"operation executed {m} times")
                    if not x.h.audit.verify()[0]:
                        got.append("audit chain broken")
                finally:
                    x.close()
                if got:
                    violations[fn.__name__] = got
                self.assertEqual(got, [])
        print(f"\nSCHEDULER_ADVERSARIAL cases={len(CASES)} violations={len(violations)} max_begins_per_op={worst}",
              file=sys.stderr)
        self.assertEqual(violations, {})


if __name__ == "__main__":
    unittest.main()
