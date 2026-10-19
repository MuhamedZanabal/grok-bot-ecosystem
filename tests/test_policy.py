import unittest

from tests.helpers import Harness
from control_plane import Capability, PolicyEngine, RiskLevel, Status


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_default_deny_without_grant(self):
        out = self.h.cp.execute(self.h.req("cap.fs.read"))
        self.assertEqual(out.status, Status.DENIED)
        self.assertEqual(out.reason, "deny:no_grant")
        self.assertEqual(self.h.total_calls(), 0)

    def test_unknown_capability_denied(self):
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        out = self.h.cp.execute(self.h.req("cap.shell.exec"))
        self.assertEqual(out.status, Status.DENIED)
        self.assertEqual(out.reason, "deny:unknown_capability")

    def test_grant_allows_level0_and_level1(self):
        for cap in ("cap.fs.read", "cap.fs.write"):
            self.h.policy.grant("agent.alpha", cap)
            out = self.h.cp.execute(self.h.req(cap, op=f"op-{cap}"))
            self.assertEqual(out.status, Status.EXECUTED, out)
        self.assertEqual(self.h.total_calls(), 2)

    def test_grant_is_per_actor(self):
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        out = self.h.cp.execute(self.h.req("cap.fs.read", actor="agent.beta"))
        self.assertEqual(out.status, Status.DENIED)

    def test_revoke_restores_deny(self):
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        self.h.policy.revoke("agent.alpha", "cap.fs.read")
        self.assertEqual(self.h.cp.execute(self.h.req("cap.fs.read")).status, Status.DENIED)

    def test_cannot_grant_unregistered_or_wildcard(self):
        p = PolicyEngine()
        with self.assertRaises(ValueError):
            p.grant("agent.alpha", "cap.fs.read")
        with self.assertRaises(ValueError):
            Capability("cap.*.*", RiskLevel.READ)
        with self.assertRaises(ValueError):  # trailing-newline bypass of `$` (fixed via fullmatch)
            Capability("cap.fs.read\n", RiskLevel.READ)
        with self.assertRaises(ValueError):
            p.grant("agent.alpha\n", "cap.fs.read")

    def test_reregister_with_different_risk_refused(self):
        with self.assertRaises(ValueError):
            self.h.policy.register(Capability("cap.email.send", RiskLevel.READ))

    def test_unbound_handler_denied(self):
        self.h.policy.register(Capability("cap.db.read", RiskLevel.READ))
        self.h.policy.grant("agent.alpha", "cap.db.read")
        out = self.h.cp.execute(self.h.req("cap.db.read"))
        self.assertEqual((out.status, out.reason), (Status.DENIED, "deny:no_handler_bound"))


if __name__ == "__main__":
    unittest.main()
