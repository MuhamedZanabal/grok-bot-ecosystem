"""cycle-010: lead ingest/dedupe, rule scoring with evidence, template drafts without invented
facts, approval-gated queue, mock transport, outcome tracker, review export CLI."""
import json
import os
import subprocess
import sys
import unittest

from tests.outreach_helpers import ACTOR, FIX, SOURCES, OutreachHarness
from control_plane import Status
from economics import EconomicsLedger
from outreach import ingest, make_draft, norm_email, render, score
from outreach.drafts import CATEGORY_PHRASE

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
NON_SLOT = ("phone", "website", "cuisine", "street", "tech_signals", "instagram", "opening_hours")


class LeadTests(unittest.TestCase):
    def setUp(self):
        self.leads, self.stats = ingest(SOURCES)

    def test_dedupe_by_email_phone_and_name_with_provenance(self):
        self.assertEqual(self.stats, {"records": 17, "leads": 15, "merged": 2})
        alpha = [l for l in self.leads if norm_email(l["fields"].get("email", "")) == "alpha@diner.example.invalid"]
        self.assertEqual(len(alpha), 1)
        self.assertEqual(alpha[0]["sources"], ["omega.csv", "pipeline.csv"])
        self.assertTrue(alpha[0]["conflicts"])
        for l in self.leads:
            for k in l["fields"]:
                self.assertIn(k, l["provenance"])
                self.assertEqual(len(l["provenance"][k]["file_sha256"]), 64)

    def test_norm_email(self):
        self.assertEqual(norm_email(" A.B+x@Ex.example.invalid. "), "a.b@ex.example.invalid")
        for bad in ("a@b", "a b@c.d", "x@y.z\nBcc: v@w.x", "", "<a@b.cd>"):
            self.assertEqual(norm_email(bad), "")

    def test_scores_are_rule_sums_with_evidence(self):
        for l in self.leads:
            s = score(l)
            self.assertEqual(s["score"], sum(e["points"] for e in s["evidence"]))
            for e in s["evidence"]:
                self.assertEqual(l["fields"][e["field"]], e["value"])
                self.assertEqual(e["source"], l["provenance"][e["field"]])
        alpha = next(l for l in self.leads if l["fields"]["name"] == "Alpha Diner")
        self.assertEqual({e["rule"] for e in score(alpha)["evidence"]},
                         {"has_email", "has_phone", "food_service", "multi_branch", "live_website", "hours_listed"})

    def test_drafts_never_invent_lead_facts(self):
        drafts = [d for d in (make_draft(l, score(l)) for l in self.leads) if d]
        self.assertEqual(len(drafts), 8)
        by = {l["lead_id"]: l for l in self.leads}
        for d in drafts:
            lead = by[d["lead_id"]]
            values = {}
            for p in d["personalization"]:
                self.assertEqual(lead["fields"][p["field"]], p["source_value"])
                self.assertEqual(lead["provenance"][p["field"]], p["source"])
                if p["slot"] == "category_phrase":
                    self.assertEqual(CATEGORY_PHRASE[p["source_value"]], p["value"])
                elif p["slot"] != "multi_branch":
                    self.assertEqual(p["value"], p["source_value"])
                values[p["slot"]] = p["value"]
            self.assertEqual(render(values), (d["subject"], d["body"]))   # nothing beyond the slots
            for k in NON_SLOT:
                v = lead["fields"].get(k)
                if v and len(v) > 3:
                    self.assertNotIn(v, d["body"])
            self.assertEqual(d["recipient"], lead["fields"]["email"])


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.o = OutreachHarness()

    def tearDown(self):
        self.o.close()

    def test_build_enqueues_only_pending_approval_and_is_idempotent(self):
        st = self.o.build()
        self.assertEqual((st["drafts_created"], st["pending_approval"], st["suppressed"], st["prior_pending"],
                          st["held"]), (8, 8, 2, 1, 2))
        self.assertEqual({d["state"] for d in self.o.store.drafts()}, {"pending_approval"})
        self.assertTrue(all(d["intact"] for d in self.o.pending()))
        self.assertEqual(self.o.build()["drafts_existing"], 8)
        self.assertEqual(len(self.o.store.drafts()), 8)
        self.assertEqual(self.o.store.suppressed("BOUNCE@bistro.example.invalid"), "bounced")
        self.assertEqual(self.o.transport.sent, [])

    def test_approved_send_goes_once_through_mock_and_is_costed(self):
        led = EconomicsLedger(os.path.join(self.o.h.dir.name, "econ.sqlite"), b"e" * 32, self.o.h.audit,
                              clock=self.o.h.wall)
        self.o.h.cp.economics = led
        self.o.build()
        d = self.o.pending()[0]
        out = self.o.send(d, self.o.token(d))
        self.assertEqual(out.status, Status.EXECUTED, out.reason)
        self.assertTrue(out.result["sent"])
        self.assertEqual(len(self.o.transport.sent), 1)
        self.assertEqual(self.o.transport.sent[0][1], d["recipient"])
        self.assertEqual(self.o.store.draft(d["draft_id"])["state"], "sent")
        rows = [e for e in led.entries() if e["kind"] == "operation" and e["item"] == "cap.outreach.send"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["risk"]["value"], rows[0]["actual_revenue"]["provenance"]), (3, "unknown"))

    def test_enqueue_refuses_suppressed_or_invalid_recipient(self):
        self.o.build()
        d = self.o.pending()[0]
        args = {k: d[k] for k in ("lead_id", "channel", "subject", "body", "personalization", "meta")}
        self.o.store.suppress("someone@x.example.invalid", "operator", "test", 0)
        for i, rcpt in enumerate(("Someone+a@X.example.invalid", "not-an-address")):
            out = self.o.h.cp.execute({"operation_id": f"enq-supp-{i}", "actor": ACTOR, "capability": "cap.outreach.enqueue",
                                       "args": {**args, "recipient": rcpt, "template_id": f"t{i}"}})
            self.assertEqual(out.status, Status.FAILED)
        self.assertEqual(len(self.o.store.drafts()), 8)

    def test_outcomes_default_unknown_and_opt_out_suppresses(self):
        self.o.build()
        d = self.o.pending()[0]
        o = self.o.store.outcome(d["lead_id"])
        self.assertEqual((o["outcome"], o["reply_state"], o["revenue"]["value"]), ("UNKNOWN", "UNKNOWN", None))
        out = self.o.h.cp.execute({"operation_id": "outcome-0001", "actor": ACTOR, "capability": "cap.outreach.outcome",
                                   "args": {"lead_id": d["lead_id"], "outcome": "opted_out", "evidence": "replied stop"}})
        self.assertEqual(out.result["reply_state"], "replied")
        self.assertEqual(self.o.store.suppressed(d["recipient"]), "opted_out")
        self.assertEqual(self.o.store.draft(d["draft_id"])["state"], "rejected")


class CliTests(unittest.TestCase):
    def test_build_and_export_cli(self):
        o = OutreachHarness()
        try:
            st = os.path.join(o.h.dir.name, "state")
            env = {**os.environ, "PYTHONPATH": SRC}
            run = lambda *a: subprocess.run([sys.executable, "-m", "outreach", *a], capture_output=True, text=True,
                                            env=env, timeout=60)
            p = run("build", "--state", st, "--pipeline", os.path.join(FIX, "pipeline.csv"),
                    "--omega", os.path.join(FIX, "omega.csv"))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["pending_approval"], 8)
            j, m = os.path.join(st, "p.jsonl"), os.path.join(st, "p.md")
            p = run("export", "--state", st, "--jsonl", j, "--md", m)
            self.assertEqual(json.loads(p.stdout), {"exported_pending": 8})
            recs = [json.loads(x) for x in open(j, encoding="utf-8")]
            self.assertEqual({r["state"] for r in recs}, {"pending_approval"})
            md = open(m, encoding="utf-8").read()
            self.assertIn("Nothing has been sent", md)
            for r in recs:
                self.assertIn(r["draft_id"], md)
                self.assertIn(r["draft_hash"], md)
            self.assertEqual(oct(os.stat(os.path.join(st, "outbox.key")).st_mode & 0o777), "0o600")
            begins = [json.loads(x)["data"]["capability"] for x in open(os.path.join(st, "audit.jsonl"))
                      if '"event":"execute_begin"' in x]
            self.assertEqual(set(begins), {"cap.outreach.enqueue"})      # the build never attempts a send
            print(f"OUTREACH_CLI pending={len(recs)} exported_jsonl={len(recs)} sends=0")
        finally:
            o.close()


if __name__ == "__main__":
    unittest.main()
