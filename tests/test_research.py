"""cycle-007 research/evidence engine: source registry rules, lifecycle with evidence,
deterministic cited report, Tier 1 via MockProvider, and branch_closed (local fixtures)."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

from tests.research_helpers import ResearchHarness, fixture
from orchestrator import MockProvider
from research import CLOSE_AFTER, ClaimLedger, SourceError, SourceRegistry, source_id_for, write_report

Q, METRIC = "lowest p50 parser", "p50_ms"
PASSES = (["/c/alpha", "/c/zeta"], ["/c/beta"], ["/c/gamma", "/c/eps"], ["/c/delta"],
          ["/c/omega-a", "/c/omega-b"])


def full_run(r):
    bid = r.run.open_branch(Q, METRIC, "min", 1.0, attributes=("license",), require={"license": ["MIT"]})
    outs = [r.run.discovery_pass(bid, [r.url(u) for u in urls]) for urls in PASSES]
    return bid, outs


class SourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "s.sqlite")
        self.reg = SourceRegistry(self.path)

    def tearDown(self):
        self.reg.close()
        self.dir.cleanup()

    def test_defaults_exact_url_stable_id_persisted(self):
        url = "http://127.0.0.1:9/Path/?q=A%20b&x=1#frag"
        row, created = self.reg.register(url, "web_page", 1.0)
        self.assertTrue(created)
        self.assertEqual((row["status"], row["trusted"], row["execution_allowed"]), ("candidate", False, False))
        self.assertEqual(row["url"], url)
        self.assertEqual(row["source_id"], source_id_for(url))
        self.assertFalse(self.reg.register(url, "web_page", 2.0)[1])  # idempotent, same id
        self.reg.close()
        self.reg = SourceRegistry(self.path)
        self.assertEqual(self.reg.get(source_id_for(url))["url"], url)

    def test_url_variants_are_distinct_sources(self):
        base = "http://127.0.0.1:9/c/alpha"
        ids = {source_id_for(u) for u in (base, base + "/", base.upper(), base + "?", base + " ".strip() + "#",
                                          "http://127.0.0.1:9/c/Alpha")}
        self.assertEqual(len(ids), 6)

    def test_trust_columns_cannot_be_raised_even_by_sql(self):
        row, _ = self.reg.register("http://127.0.0.1:9/a", "web_page", 1.0)
        db = sqlite3.connect(self.path)
        for col in ("trusted", "execution_allowed"):
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(f"UPDATE sources SET {col}=1 WHERE source_id=?", (row["source_id"],))
        db.close()

    def test_lifecycle_rules_in_the_registry(self):
        sid = self.reg.register("http://127.0.0.1:9/a", "web_page", 1.0)[0]["source_id"]
        with self.assertRaises(SourceError) as e:
            self.reg.transition(sid, "evaluating", "x", [], 2.0)
        self.assertEqual(e.exception.code, "transition_without_evidence")
        with self.assertRaises(SourceError) as e:
            self.reg.transition(sid, "accepted", "x", [{"kind": "record", "id": "r"}], 2.0)
        self.assertEqual(e.exception.code, "illegal_transition")
        self.reg.transition(sid, "evaluating", "x", [{"kind": "record", "id": "r"}], 2.0)
        self.assertEqual([(t["from"], t["to"]) for t in self.reg.transitions(sid)], [("candidate", "evaluating")])

    def test_secret_url_refused(self):
        with self.assertRaises(SourceError):
            self.reg.register("http://127.0.0.1:9/a?token=ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "web_page", 1)


class ResearchRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def setUp(self):
        self.r = ResearchHarness(self.fx)

    def tearDown(self):
        self.r.close()

    def test_lifecycle_is_evidence_backed_and_audited(self):
        bid, _ = full_run(self.r)
        st = {s["url"].rsplit("/", 1)[-1]: s["status"] for s in self.r.sources.all()}
        self.assertEqual(st, {"alpha": "accepted", "beta": "accepted", "gamma": "accepted", "delta": "accepted",
                              "zeta": "rejected", "eps": "deferred", "omega-a": "deferred", "omega-b": "deferred"})
        rows = 0
        for s in self.r.sources.all():
            self.assertEqual((s["trusted"], s["execution_allowed"]), (False, False))
            for t in self.r.sources.transitions(s["source_id"]):
                rows += 1
                self.assertTrue(json.loads(t["evidence"]))
        self.assertEqual(len(self.r.audit_events("source_transition")), rows)
        self.assertEqual(self.r.h.audit.verify()[0], True)

    def test_branch_closed_after_three_non_superior_passes(self):
        bid, outs = full_run(self.r)
        self.assertEqual([o["superior"] for o in outs], [True, True, False, False, False])
        self.assertEqual([o["status"] for o in outs], ["open"] * 4 + ["closed"])
        hits = len(self.fx.hits)
        again = self.r.run.discovery_pass(bid, [self.r.url("/c/alpha"), self.r.url("/c/beta")])
        self.assertEqual(again, {"refused": "branch_closed", "fetches": 0})
        self.assertEqual(len(self.fx.hits), hits)  # no fetch reached the fixture
        ev = self.r.audit_events("branch_closed")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["passes"], 5)
        self.assertEqual(CLOSE_AFTER, 3)
        led = ClaimLedger(os.path.join(self.r.h.dir.name, "claims.sqlite"))  # persisted
        b = led.branch(bid)
        led.close()
        self.assertEqual((b["status"], b["best_value"], b["stale_passes"]), ("closed", 30.0, 3))
        print(f"\nBRANCH_CLOSED passes={b['passes']} stale={b['stale_passes']} metric={b['metric']} "
              f"best={b['best_value']} reason={b['closed_reason']} refused_pass_fetches={again['fetches']}",
              file=sys.stderr)

    def test_superior_requires_beating_incumbent_by_min_delta(self):
        bid = self.r.run.open_branch("q2", METRIC, "min", 1.0)
        self.assertTrue(self.r.run.discovery_pass(bid, [self.r.url("/c/beta")])["superior"])
        self.assertFalse(self.r.run.discovery_pass(bid, [self.r.url("/c/delta")])["superior"])  # 29.5 vs 30
        self.assertFalse(self.r.run.discovery_pass(bid, [self.r.url("/c/eps")])["superior"])  # no metric
        self.assertEqual(self.r.ledger.branch(bid)["stale_passes"], 2)

    def test_report_every_claim_cited_and_resolvable(self):
        bid, _ = full_run(self.r)
        rep = self.r.run.report(bid)
        self.assertTrue(rep["claims"])
        cites = {c["n"]: c for c in rep["citations"]}
        for c in rep["claims"]:
            self.assertTrue(c["citations"])
            for n in c["citations"]:
                row, why = self.r.mem.get(cites[n]["record_id"])
                self.assertIsNone(why)
                self.assertEqual(row["content_sha256"], cites[n]["content_sha256"])
                self.assertEqual(row["origin_url"], cites[n]["url"])
                self.assertIn(cites[n]["quote"], row["content"])
            if c["verdict"] != "verified":
                self.assertEqual(c["value"], "UNKNOWN")
        self.assertEqual(rep["best_claim_still_verified"], True)

    def test_tier1_mock_model_claim_passes_the_same_verifier(self):
        model = MockProvider([json.dumps({"type": "claims", "claims": [
            {"subject": "theta", "attribute": "p50_ms", "value": "25", "quote": "p50_ms: 25"}]})])
        self.r.run.provider = model
        bid = self.r.run.open_branch("q3", METRIC)
        out = self.r.run.discovery_pass(bid, [self.r.url("/c/theta")])
        self.assertTrue(out["superior"], out)
        self.assertEqual(len(model.calls), 1)
        req = json.loads(model.calls[0])
        self.assertEqual(req["untrusted_data"]["type"], "untrusted_data")
        claims = self.r.ledger.claims(bid)
        self.assertEqual([(c["value"], self.r.verifier.verdict(c)[0]) for c in claims], [("25", "verified")])


class DeterminismTests(unittest.TestCase):
    def test_two_fresh_runs_produce_identical_report_bytes(self):
        fx = fixture()
        shas = []
        try:
            for _ in range(2):
                r = ResearchHarness(fx)
                try:
                    bid, _ = full_run(r)
                    rep = r.run.report(bid)
                    out = write_report(rep, os.path.join(r.h.dir.name, "report"))
                    with open(out["paths"]["report.md"]) as fh:
                        self.assertIn("[1]", fh.read())
                    shas.append((out["json_sha256"], out["md_sha256"], len(rep["claims"]), len(rep["unknowns"]),
                                 len(rep["citations"])))
                finally:
                    r.close()
        finally:
            fx.close()
        self.assertEqual(shas[0], shas[1])
        print(f"\nRESEARCH_REPORT json_sha256={shas[0][0][:16]} md_sha256={shas[0][1][:16]} claims={shas[0][2]} "
              f"unknown={shas[0][3]} citations={shas[0][4]} identical_runs=2", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
