import json
import os
import subprocess
import sys
import unittest

from tests.helpers import FAKE_GH_TOKEN, Harness
from control_plane import AuditLog, Status, redact, verify_chain

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.email.send")

    def tearDown(self):
        self.h.close()

    def _raw(self):
        with open(self.h.audit_path, encoding="utf-8") as fh:
            return fh.read()

    def test_every_decision_is_audited(self):
        self.h.cp.execute(self.h.req("cap.fs.read"))                 # denied
        self.h.cp.execute(self.h.req("cap.email.send"))              # approval required
        tok = self.h.approve("cap.email.send")
        self.h.cp.execute(self.h.req("cap.email.send", token=tok))   # executed
        events = [json.loads(line) for line in self._raw().splitlines()]
        statuses = [e["data"].get("status") for e in events if e["event"] == "decision"]
        self.assertEqual(statuses, ["denied", "approval_required", "executed"])
        self.assertIn("execute_begin", [e["event"] for e in events])

    def test_secrets_never_reach_disk(self):
        tok = self.h.approve("cap.email.send")
        self.h.audit.append("probe", api_key="hunter2-plain", note=f"use {FAKE_GH_TOKEN} please",
                            headers={"Authorization": "Bearer abcdefghijklmnop"},
                            nested=[{"password": "p@ss"}, "sk-" + "x" * 24], approval_token=tok)
        raw = self._raw()
        for secret in ("hunter2-plain", FAKE_GH_TOKEN, "abcdefghijklmnop", "p@ss", "sk-" + "x" * 24, tok):
            self.assertNotIn(secret, raw)
        self.assertIn("[REDACTED]", raw)

    def test_redact_handles_private_keys_and_keeps_safe_values(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
        out = redact({"blob": pem, "count": 3, "subject": "hello"})
        self.assertEqual(out, {"blob": "[REDACTED]", "count": 3, "subject": "hello"})

    def test_chain_verifies_and_detects_tampering(self):
        for i in range(5):
            self.h.audit.append("e", i=i)
        self.assertEqual(verify_chain(self.h.audit_path), (True, 5, ""))
        lines = self._raw().splitlines()
        rec = json.loads(lines[2])
        rec["data"]["i"] = 999
        lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        with open(self.h.audit_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        ok, _, err = verify_chain(self.h.audit_path)
        self.assertFalse(ok)
        self.assertEqual(err, "hash_mismatch_at_seq_3")
        with self.assertRaises(RuntimeError):
            AuditLog(self.h.audit_path)  # refuses to append onto a broken chain

    def test_deleted_record_detected(self):
        for i in range(3):
            self.h.audit.append("e", i=i)
        lines = self._raw().splitlines()
        del lines[1]
        with open(self.h.audit_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self.assertEqual(verify_chain(self.h.audit_path)[2], "chain_break_at_seq_2")

    def test_reopen_continues_chain(self):
        self.h.audit.append("a")
        AuditLog(self.h.audit_path).append("b")
        self.assertEqual(verify_chain(self.h.audit_path), (True, 2, ""))

    def test_cli_verify(self):
        self.h.audit.append("a")
        env = dict(os.environ, PYTHONPATH=SRC)
        r = subprocess.run([sys.executable, "-m", "control_plane", "audit-verify", self.h.audit_path],
                           env=env, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok records=1"))


if __name__ == "__main__":
    unittest.main()
