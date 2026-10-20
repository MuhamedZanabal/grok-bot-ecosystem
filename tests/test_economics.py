"""cycle-009: economic telemetry, spend guards, dry run, status report, redacted export."""
import json
import os
import subprocess
import sys
import unittest

from tests.economics_helpers import ECON_KEY, SECRET, UsageProvider, attach, rows, sql
from tests.helpers import Harness, act, final
from tests.scheduler_helpers import SCHED_KEY, Crash, SchedHarness
from control_plane import Status
from control_plane.handlers import HandlerFailure
from economics import (Caps, EconomicsLedger, QuantityError, check, estimate, export_jsonl, measured,
                       render_markdown, status_report, total, unknown)
from economics.ledger import FIELDS
from integrity import crosscheck
from orchestrator import AgentRunner, RunConfig

T0 = 1_000_020  # a multiple of 60, so interval slots land on T0 + 60k
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


class QuantityTests(unittest.TestCase):
    def test_unknown_iff_null_and_validation(self):
        self.assertIsNone(unknown("usd", "x")["value"])
        for bad in ({"value": 0, "provenance": "unknown", "unit": "usd", "source": "s"},
                    {"value": None, "provenance": "measured", "unit": "usd", "source": "s"},
                    {"value": -1, "provenance": "measured", "unit": "usd", "source": "s"},
                    {"value": True, "provenance": "measured", "unit": "usd", "source": "s"},
                    {"value": float("nan"), "provenance": "measured", "unit": "usd", "source": "s"},
                    {"value": 1, "provenance": "guess", "unit": "usd", "source": "s"},
                    {"value": 2, "provenance": "measured", "unit": "ratio", "source": "s"},
                    {"value": 1, "provenance": "measured", "unit": "usd"}):
            with self.assertRaises(QuantityError):
                check(bad)

    def test_total_never_coerces_unknown(self):
        t = total([measured(1, "usd", "a"), unknown("usd", "b"), estimate(2, "usd", "c")], "usd")
        self.assertIsNone(t["value"])
        self.assertEqual((t["unknown"], t["known"], t["known_sum"], t["provenance"]), (1, 2, 3, "unknown"))
        t = total([measured(1, "usd", "a"), estimate(2, "usd", "c")], "usd")
        self.assertEqual((t["value"], t["provenance"]), (3, "configured_estimate"))
        self.assertEqual(total([measured(1, "s", "a")], "s")["provenance"], "measured")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        self.h.policy.grant("agent.alpha", "cap.fs.write")
        self.led = attach(self.h, per_call={"cap.fs.read": 0.01},
                          expected={"cap.fs.read": {"expected_value": 0.5, "confidence": 0.7}})

    def tearDown(self):
        self.h.close()

    def test_every_executed_failed_timed_out_op_has_one_complete_row(self):
        def boom(a):
            raise HandlerFailure("bad")
        self.h.cp.bind("cap.fs.write", boom)
        outs = [self.h.cp.execute(self.h.req("cap.fs.read", op=f"op-read-{i:04d}")) for i in range(3)]
        outs.append(self.h.cp.execute(self.h.req("cap.fs.write", op="op-write-0001")))
        outs.append(self.h.cp.execute(self.h.req("cap.fs.read", op="op-read-0000")))   # replay: no new row
        outs.append(self.h.cp.execute(self.h.req("cap.email.send", op="op-denied-01")))  # denied: no row
        self.assertEqual([o.status for o in outs], [Status.EXECUTED] * 3 + [Status.FAILED, Status.REPLAYED,
                                                                            Status.DENIED])
        ops = [e for e in self.led.entries() if e["kind"] == "operation"]
        self.assertEqual(sorted(e["subject"] for e in ops), ["op-read-0000", "op-read-0001", "op-read-0002",
                                                             "op-write-0001"])
        for e in ops:
            for f in FIELDS:
                self.assertIn(f, e)
                for qv in (e[f].values() if f == "actual_cost" else [e[f]]):
                    check(qv)
            self.assertEqual(e["actual_revenue"]["provenance"], "unknown")
            self.assertEqual(e["actual_cost"]["wall_s"]["provenance"], "measured")
            self.assertEqual(e["actual_cost"]["usd"]["provenance"], "unknown")
        r = next(e for e in ops if e["subject"] == "op-read-0001")
        self.assertEqual(r["estimated_cost"]["value"], 0.01)
        self.assertEqual(r["expected_value"]["provenance"], "configured_estimate")
        self.assertEqual(r["expected_revenue"]["provenance"], "unknown")
        self.assertEqual(r["risk"]["value"], 0)
        w = next(e for e in ops if e["subject"] == "op-write-0001")
        self.assertEqual((w["status"], w["reason"]), ("failed", "worker:bad"))
        self.assertEqual(w["estimated_cost"]["provenance"], "unknown")
        self.assertEqual(len(rows(self.h.econ_path, "reservation")), 4)
        self.assertTrue(crosscheck(self.h.audit_path, {"economics": self.led})["ok"])

    def test_egress_bytes_metered_from_proxy_audit_events(self):
        def fetch(a):
            self.h.audit.append("egress_closed", capability="cap.fs.read", host="h", port=443,
                                bytes_out=40, bytes_in=960)
            self.h.audit.append("egress_closed", capability="cap.other.x", bytes_out=5, bytes_in=5)
            return {"ok": True, "bytes": 999999}            # worker-reported numbers are ignored
        self.h.cp.bind("cap.fs.read", fetch)
        self.h.cp.execute(self.h.req("cap.fs.read", op="op-egress-01"))
        self.h.cp.execute(self.h.req("cap.fs.write", op="op-egress-02"))
        e = {x["subject"]: x for x in self.led.entries() if x["kind"] == "operation"}
        self.assertEqual(e["op-egress-01"]["actual_cost"]["egress_bytes"],
                         measured(1000, "bytes", "host:egress_proxy_audit"))
        self.assertEqual(e["op-egress-02"]["actual_cost"]["egress_bytes"]["value"], 0)

    def test_dry_run_checks_everything_and_has_no_side_effects(self):
        n_audit_begin = lambda: sum(1 for ln in open(self.h.audit_path) if '"event":"execute_begin"' in ln)
        it, before = self.h.governor.iterations, len(rows(self.h.econ_path))
        out = self.h.cp.execute({**self.h.req("cap.fs.read", op="op-dry-0001"), "dry_run": True})
        self.assertEqual(out.status, Status.DRY_RUN)
        self.assertEqual(out.result["estimated_cost"]["value"], 0.01)
        self.assertEqual((self.h.total_calls(), self.h.governor.iterations, len(rows(self.h.econ_path))),
                         (0, it, before))
        self.assertIsNone(self.h.ledger.get("op-dry-0001"))
        self.assertEqual(n_audit_begin(), 0)
        self.assertEqual(self.h.cp.execute({**self.h.req("cap.email.send", op="op-dry-0002"), "dry_run": True}).status,
                         Status.DENIED)
        self.h.policy.grant("agent.alpha", "cap.payment.transfer")
        self.assertEqual(self.h.cp.execute({**self.h.req("cap.payment.transfer", op="op-dry-0003"),
                                            "dry_run": True}).status, Status.APPROVAL_REQUIRED)
        self.assertEqual(self.h.cp.execute({**self.h.req("cap.fs.read", op="op-dry-0004"), "dry_run": 1}).reason,
                         "bad_dry_run")
        self.h.kill.engage("t")
        self.assertEqual(self.h.cp.execute({**self.h.req("cap.fs.read", op="op-dry-0005"), "dry_run": True}).status,
                         Status.KILLED)
        # the real call afterwards executes once
        self.h.kill.release()
        self.assertEqual(self.h.cp.execute(self.h.req("cap.fs.read", op="op-dry-0001")).status, Status.EXECUTED)

    def test_budget_scope_validation(self):
        for bad in ({"tenant": "x"}, {"run": ""}, {"run": "a b"}, {"run": 5}, []):
            r = {**self.h.req("cap.fs.read", op="op-scope-01"), "budget_scope": bad}
            self.assertEqual(self.h.cp.execute(r).reason, "bad_budget_scope")


class CapTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        self.h.policy.grant("agent.beta", "cap.fs.read")

    def tearDown(self):
        self.h.close()

    def ex(self, op, actor="agent.alpha", scope=None):
        r = self.h.req("cap.fs.read", op=op, actor=actor)
        if scope:
            r["budget_scope"] = scope
        return self.h.cp.execute(r)

    def test_day_caps_per_actor_global_and_rollover(self):
        attach(self.h, Caps(day_usd=0.25, day_usd_global=0.35), per_call={"cap.fs.read": 0.1})
        st = [self.ex(f"op-day-a{i}").status for i in range(3)]
        self.assertEqual(st, [Status.EXECUTED, Status.EXECUTED, Status.DENIED])
        self.assertEqual(self.ex("op-day-a9").reason, "budget:day_usd_cap")
        self.assertEqual(self.ex("op-day-b0", "agent.beta").status, Status.EXECUTED)
        self.assertEqual(self.ex("op-day-b1", "agent.beta").reason, "budget:day_usd_global_cap")
        self.h.wall.t += 86400
        self.assertEqual(self.ex("op-day-a3").status, Status.EXECUTED)

    def test_run_and_job_caps(self):
        attach(self.h, Caps(run_usd=0.2, job_usd=0.3, run_calls=5), per_call={"cap.fs.read": 0.1})
        self.assertEqual(self.ex("op-run-0000").reason, "budget:run_scope_required")
        self.assertEqual([self.ex(f"op-run-r{i}", scope={"run": "r1"}).status for i in range(3)],
                         [Status.EXECUTED, Status.EXECUTED, Status.DENIED])
        sc = lambda r: {"run": r, "job": "j1"}
        self.assertEqual([self.ex(f"op-job-{r}", scope=sc(r)).reason for r in ("a", "b", "c", "d")],
                         ["executed", "executed", "executed", "budget:job_usd_cap"])

    def test_call_count_caps_with_unpriced_capability(self):
        attach(self.h, Caps(day_calls=2))
        self.assertEqual([self.ex(f"op-cnt-000{i}").reason for i in range(3)],
                         ["executed", "executed", "budget:day_calls_cap"])

    def test_scheduler_runs_carry_job_and_run_scope(self):
        sh = SchedHarness()
        try:
            led = attach(sh.h, Caps(job_usd=0.25), per_call={"cap.job.step": 0.1})
            sh.at(T0)
            sh.define("capped", every=60)
            for t in (T0 + 60, T0 + 120, T0 + 180, T0 + 240):
                sh.at(t)
                sh.s.tick()
            states = [r["state"] for r in sh.s.store.runs("capped")]
            self.assertEqual(states, ["succeeded", "succeeded", "failed", "failed"])
            self.assertIn("budget:job_usd_cap", sh.s.store.runs("capped")[2]["reason"])
            jobs = {e["job"] for e in led.entries() if e["kind"] == "reservation"}
            self.assertEqual(jobs, {"capped"})
        finally:
            sh.close()


class ModelMeterTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.fs.read")

    def tearDown(self):
        self.h.close()

    def runner(self, prov, **cfg):
        return AgentRunner(self.h.cp.gateway("agent.alpha"), [prov], self.h.audit, RunConfig(**cfg), self.h.kill,
                           clock=self.h.mono, meter=self.h.cp.economics)

    def test_usage_fields_metered_and_missing_usage_is_unknown(self):
        led = attach(self.h, token={"mockusage": {"in_per_1k": 1.0, "out_per_1k": 2.0}})
        prov = UsageProvider([act("cap.fs.read"), final()], [{"input_tokens": 1000, "output_tokens": 500}, None])
        self.assertEqual(self.runner(prov).run("t", "run-meter-1").state.value, "completed")
        mc = [e for e in led.entries() if e["kind"] == "model_call"]
        self.assertEqual(len(mc), 2)
        a, b = (m["actual_cost"] for m in mc)
        self.assertEqual((a["usage_in"]["value"], a["usage_out"]["value"], a["usd"]["value"]), (1000, 500, 2.0))
        self.assertEqual(a["usd"]["provenance"], "configured_estimate")
        self.assertEqual((b["usage_in"]["provenance"], b["usd"]["provenance"]), ("unknown", "unknown"))
        op = next(e for e in led.entries() if e["kind"] == "operation")
        self.assertEqual(op["run"], "run-meter-1")
        self.assertEqual(led.burn()["model_calls"], 2)

    def test_model_spend_cap_aborts_run_before_provider_call(self):
        attach(self.h, Caps(day_usd=0.05, run_usd=1.0), per_call={"model:mockusage": 0.03, "cap.fs.read": 0.0})
        prov = UsageProvider([act("cap.fs.read"), act("cap.fs.read", {"y": 1}), final()], [])
        res = self.runner(prov).run("t", "run-meter-2")
        self.assertEqual((res.state.value, res.reason), ("cycle_aborted", "budget:day_usd_cap"))
        self.assertEqual(len(prov.calls), 1)


class StatusTests(unittest.TestCase):
    def test_status_report_lists_every_attention_class(self):
        sh = SchedHarness()
        try:
            h = sh.h
            led = attach(h, Caps(day_usd_global=0.1), per_call={"cap.job.step": 0.05, "cap.job.crash": 0.09})
            sh.at(T0)
            sh.define("crashy", [{"capability": "cap.job.crash", "args": {}}], every=60)
            sh.define("gappy", every=60)
            sh.define("tampered", every=60)
            sh.at(T0 + 60)
            sh.crash_next = 1
            with self.assertRaises(Crash):
                sh.s.tick()
            sh.at(T0 + 100000)
            sh.s = sh.new(owner="sched-2")
            sh.s.tick()
            sql(sh.path, "UPDATE jobs SET spec=replace(spec, 'agent.jobs', 'agent.root') WHERE job_id='tampered'")
            sh.s.tick()
            h.kill.engage("operator stop")
            sql(h.econ_path, "DELETE FROM entries WHERE seq=(SELECT MAX(seq) FROM entries)")
            kf = os.path.join(h.dir.name, "k")
            open(kf, "wb").write(SCHED_KEY)
            ef = os.path.join(h.dir.name, "ek")
            open(ef, "wb").write(ECON_KEY)
            cfg = {"audit": h.audit_path, "kill_switch": h.kill_path,
                   "scheduler": {"db": sh.path, "key_file": kf},
                   "economics": {"db": h.econ_path, "key_file": ef, "caps": {"day_usd_global": 0.1},
                                 "day": "1970-01-12"}}
            rep = status_report(cfg)
            att = rep["attention"]
            self.assertIn("kill_switch_engaged", att)
            self.assertIn(f"in_doubt:crashy@{T0 + 60}", att)
            self.assertIn("quarantined:tampered", att)
            self.assertTrue(any(a.startswith("skipped_gaps:") for a in att))
            self.assertIn("crosscheck_failed:economics", att)
            self.assertTrue(any(a.startswith(f"reservation_without_outcome:job:crashy:{T0 + 60}:0") for a in att))
            self.assertIn("day_usd_global_burn>=80%", att)
            self.assertIsNone(rep["budget"]["actual_usd"]["value"])
            md = render_markdown(rep)
            for s in ("# Operator status", "## Scheduler", "## Budget burn", "## Cross-check", "UNKNOWN",
                      "in_doubt runs: 1", "quarantined: ['tampered']"):
                self.assertIn(s, md)
            cp = os.path.join(h.dir.name, "cfg.json")
            json.dump(cfg, open(cp, "w"))
            out = os.path.join(h.dir.name, "m.jsonl")
            env = {**os.environ, "PYTHONPATH": SRC}
            p = subprocess.run([sys.executable, "-m", "economics", "status", "--config", cp, "--export-jsonl", out],
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertEqual(json.loads(p.stdout)["attention"], att)
            lines = [json.loads(x) for x in open(out)]
            self.assertEqual(lines[0]["type"], "status")
            self.assertTrue(all(x["type"] == "economics_entry" for x in lines[1:]) and len(lines) > 1)
            p = subprocess.run([sys.executable, "-m", "economics", "status", "--config", cp, "--format", "md"],
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertIn("## Budget burn", p.stdout)
            print(f"STATUS_REPORT attention={len(att)} classes={sorted({a.split(':')[0] for a in att})}")
        finally:
            sh.close()

    def test_clean_status_exits_zero(self):
        h = Harness()
        try:
            h.policy.grant("agent.alpha", "cap.fs.read")
            attach(h, per_call={"cap.fs.read": 0.0})
            h.cp.execute(h.req("cap.fs.read"))
            ef = os.path.join(h.dir.name, "ek")
            open(ef, "wb").write(ECON_KEY)
            rep = status_report({"audit": h.audit_path, "kill_switch": h.kill_path,
                                 "economics": {"db": h.econ_path, "key_file": ef, "day": "1970-01-12"}})
            self.assertEqual(rep["attention"], ["actual_usd_unknown:1"])
            rep = status_report({"audit": h.audit_path})
            self.assertEqual(rep["kill_switch"]["engaged"], "UNKNOWN")
        finally:
            h.close()

    def test_export_redacts(self):
        h = Harness()
        try:
            p = os.path.join(h.dir.name, "x.jsonl")
            self.assertEqual(export_jsonl([{"note": f"token {SECRET}", "api_key": "plain"}], p), 1)
            body = open(p).read()
            self.assertNotIn(SECRET, body)
            self.assertNotIn("plain", body)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
