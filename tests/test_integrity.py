"""cycle-008 evidence hardening: MAC-chained claim ledger + audit-anchor cross-check.
Each tamper kind must be detected and must turn the report's claims UNKNOWN."""
import contextlib
import hashlib
import io
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import unittest

from tests.research_helpers import CLAIM_KEY, ResearchHarness, fixture
from tests.memory_helpers import MEM_KEY
from control_plane.canonical import canonical_json
from integrity import crosscheck, main
from research import ClaimLedger

PASSES = (["/c/alpha", "/c/zeta"], ["/c/beta"], ["/c/omega-a", "/c/omega-b"])


def run_research(r):
    bid = r.run.open_branch("integrity", "p50_ms", "min", 1.0, attributes=("license",), require={"license": ["MIT"]})
    for urls in PASSES:
        r.run.discovery_pass(bid, [r.url(u) for u in urls])
    return bid


def _sql(path, stmt, args=()):
    db = sqlite3.connect(path)
    db.execute(stmt, args)
    db.commit()
    db.close()


def forge_claim(path, key):
    """A key holder appends a correctly MAC'd claim that was never recorded via the CP."""
    db = sqlite3.connect(path)
    seq, prev = db.execute("SELECT seq, mac FROM claims ORDER BY seq DESC LIMIT 1").fetchone()
    cols = ("claim_id", "branch_id", "subject", "attribute", "value", "origin", "evidence", "created_at", "seq",
            "prev_mac")
    old = dict(zip(cols, db.execute(f"SELECT {', '.join(cols)} FROM claims WHERE seq=1").fetchone()))
    row = dict(old, claim_id="clm:" + "f" * 32, value="1", seq=seq + 1, prev_mac=prev)
    mac = hmac.new(key, canonical_json(row).encode(), hashlib.sha256).hexdigest()
    db.execute(f"INSERT INTO claims ({', '.join(cols)}, mac) VALUES ({', '.join('?' * (len(cols) + 1))})",
               tuple(row[c] for c in cols) + (mac,))
    db.commit()
    db.close()


TAMPERS = {
    "claim_edit": lambda r: _sql(r.claims_path, "UPDATE claims SET value='1' WHERE seq=2"),
    "claim_delete_middle": lambda r: _sql(r.claims_path, "DELETE FROM claims WHERE seq=2"),
    "claim_delete_tail": lambda r: _sql(r.claims_path, "DELETE FROM claims WHERE seq=(SELECT MAX(seq) FROM claims)"),
    "claim_forged_insert_with_key": lambda r: forge_claim(r.claims_path, CLAIM_KEY),
    "memory_truncate_tail": lambda r: _sql(r.h.mem_path, "DELETE FROM records WHERE seq=(SELECT MAX(seq) FROM records)"),
    "memory_edit_content": lambda r: _sql(r.h.mem_path, "UPDATE records SET content='forged' WHERE seq=1"),
}
EXPECT = {"claim_edit": "claims", "claim_delete_middle": "claims", "claim_delete_tail": "claims",
          "claim_forged_insert_with_key": "claims", "memory_truncate_tail": "memory", "memory_edit_content": "memory"}


class IntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def make(self):
        r = ResearchHarness(self.fx)
        r.claims_path = os.path.join(r.h.dir.name, "claims.sqlite")
        return r, run_research(r)

    def test_ledger_requires_key(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                ClaimLedger(os.path.join(d, "c.sqlite"), b"short")

    def test_clean_run_crosschecks(self):
        r, bid = self.make()
        try:
            rep = crosscheck(r.h.audit_path, {"memory": r.mem, "claims": r.ledger})
            self.assertTrue(rep["ok"], rep)
            self.assertEqual(rep["stores"]["claims"]["rows"], rep["stores"]["claims"]["anchors"])
            self.assertEqual(rep["stores"]["memory"]["rows"], rep["stores"]["memory"]["anchors"])
            self.assertTrue(r.run.report(bid)["store_integrity_ok"])
        finally:
            r.close()

    def test_each_tamper_detected_and_voids_report(self):
        detected = {}
        for name, tamper in TAMPERS.items():
            with self.subTest(tamper=name):
                r, bid = self.make()
                try:
                    tamper(r)
                    rep = crosscheck(r.h.audit_path, {"memory": r.mem, "claims": r.ledger})
                    self.assertFalse(rep["ok"])
                    self.assertFalse(rep["stores"][EXPECT[name]]["ok"], rep)
                    report = r.run.report(bid)
                    self.assertFalse(report["store_integrity_ok"])
                    self.assertTrue(report["claims"])
                    self.assertTrue(all(c["verdict"] == "UNKNOWN" and c["value"] == "UNKNOWN" for c in report["claims"]))
                    detected[name] = rep["stores"][EXPECT[name]]
                finally:
                    r.close()
        how = {n: ("chain" if not d["chain_ok"] else "anchor") for n, d in detected.items()}
        print(f"\nLEDGER_TAMPER detected={len(detected)}/{len(TAMPERS)} via={json.dumps(how, sort_keys=True)}",
              file=sys.stderr)
        self.assertEqual(len(detected), len(TAMPERS))
        self.assertEqual((how["claim_edit"], how["claim_delete_middle"], how["memory_edit_content"]),
                         ("chain", "chain", "chain"))
        self.assertEqual(how["claim_delete_tail"], "anchor")  # chain alone cannot see tail truncation
        self.assertEqual(how["claim_forged_insert_with_key"], "anchor")
        self.assertEqual(how["memory_truncate_tail"], "anchor")

    def test_edited_claim_is_never_returned(self):
        r, bid = self.make()
        try:
            n = len(r.ledger.claims(bid))
            TAMPERS["claim_edit"](r)
            self.assertEqual(len(r.ledger.claims(bid)), n - 1)
            self.assertEqual(r.ledger.verify_chain()[2], "mac_mismatch")
        finally:
            r.close()

    def test_audit_tamper_fails_crosscheck(self):
        r, _ = self.make()
        try:
            with open(r.h.audit_path) as fh:
                lines = fh.readlines()
            rec = json.loads(lines[3])
            rec["data"]["forged"] = True
            lines[3] = json.dumps(rec) + "\n"
            with open(r.h.audit_path, "w") as fh:
                fh.writelines(lines)
            rep = crosscheck(r.h.audit_path, {"claims": r.ledger})
            self.assertEqual((rep["ok"], rep["audit_chain_ok"]), (False, False))
        finally:
            r.close()

    def test_cli_exit_codes(self):
        r, _ = self.make()
        try:
            d = r.h.dir.name
            for name, key in (("m.key", MEM_KEY), ("c.key", CLAIM_KEY)):
                with open(os.path.join(d, name), "wb") as fh:
                    fh.write(key)
            argv = ["--audit", r.h.audit_path, "--memory", r.h.mem_path, "--memory-key-file", os.path.join(d, "m.key"),
                    "--claims", r.claims_path, "--claims-key-file", os.path.join(d, "c.key")]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(argv), 0)
                TAMPERS["claim_delete_tail"](r)
                self.assertEqual(main(argv), 1)
            self.assertIn('"missing_or_altered": [', out.getvalue())
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main()
