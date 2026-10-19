import json
import unittest

from tests.helpers import Harness
from control_plane import Capability, GrantStore, PolicyEngine, RiskLevel, Status


class GrantStoreTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def _audit_events(self, prefix):
        with open(self.h.audit_path) as fh:
            return [json.loads(line) for line in fh if json.loads(line)["event"].startswith(prefix)]

    def test_grants_survive_restart(self):
        self.h.policy.grant("agent.alpha", "cap.fs.read", by="zanabal", reason="cycle-2 test")
        self.h.grant_store.close()
        store2 = GrantStore(self.h.grants_path)
        p2 = PolicyEngine(store2)
        p2.register(Capability("cap.fs.read", RiskLevel.READ))
        self.assertTrue(p2.evaluate("agent.alpha", "cap.fs.read").allowed)
        self.h.grant_store = store2

    def test_revoke_survives_restart(self):
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        self.h.policy.revoke("agent.alpha", "cap.fs.read")
        self.h.grant_store.close()
        store2 = GrantStore(self.h.grants_path)
        p2 = PolicyEngine(store2)
        p2.register(Capability("cap.fs.read", RiskLevel.READ))
        self.assertEqual(p2.evaluate("agent.alpha", "cap.fs.read").reason, "deny:no_grant")
        self.h.grant_store = store2

    def test_versions_are_monotonic_and_history_is_complete(self):
        v1 = self.h.policy.grant("agent.alpha", "cap.fs.read")
        v2 = self.h.policy.grant("agent.alpha", "cap.fs.write")
        v3 = self.h.policy.revoke("agent.alpha", "cap.fs.read")
        self.assertLess(v1, v2)
        self.assertLess(v2, v3)
        self.assertEqual(self.h.policy.version, v3)
        hist = self.h.grant_store.history()
        self.assertEqual([(e["version"], e["action"], e["capability"]) for e in hist],
                         [(v1, "grant", "cap.fs.read"), (v2, "grant", "cap.fs.write"),
                          (v3, "revoke", "cap.fs.read")])

    def test_every_grant_and_revoke_is_audited(self):
        v1 = self.h.policy.grant("agent.alpha", "cap.fs.read", by="zanabal", reason="why")
        v2 = self.h.policy.revoke("agent.alpha", "cap.fs.read", by="zanabal", reason="done")
        events = self._audit_events("policy_")
        self.assertEqual([(e["event"], e["data"]["policy_version"], e["data"]["by"]) for e in events],
                         [("policy_grant", v1, "zanabal"), ("policy_revoke", v2, "zanabal")])

    def test_decisions_record_policy_version(self):
        v = self.h.policy.grant("agent.alpha", "cap.fs.read")
        self.h.cp.execute(self.h.req("cap.fs.read"))
        decision = [e for e in self._audit_events("decision")][-1]
        self.assertEqual(decision["data"]["policy_version"], v)

    def test_risk_escalation_invalidates_grant(self):
        """A grant made at risk 0 must not silently cover the same id re-registered at risk 3."""
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        p2 = PolicyEngine(self.h.grant_store)
        p2.register(Capability("cap.fs.read", RiskLevel.IRREVERSIBLE))
        d = p2.evaluate("agent.alpha", "cap.fs.read")
        self.assertEqual((d.allowed, d.reason), (False, "deny:grant_risk_mismatch"))
        self.assertEqual(p2.granted("agent.alpha"), [])

    def test_granted_lists_only_live_grants(self):
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        self.h.policy.grant("agent.alpha", "cap.email.send")
        self.h.policy.revoke("agent.alpha", "cap.fs.read")
        self.assertEqual([c.id for c in self.h.policy.granted("agent.alpha")], ["cap.email.send"])
        self.assertEqual(self.h.policy.granted("agent.beta"), [])

    def test_grant_requires_identity(self):
        with self.assertRaises(ValueError):
            self.h.policy.grant("agent.alpha", "cap.fs.read", by="")
        with self.assertRaises(ValueError):
            self.h.grant_store.apply("escalate", "a", "cap.fs.read", 0, "x", "")

    def test_revoked_grant_blocks_execution(self):
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        self.h.policy.revoke("agent.alpha", "cap.fs.read")
        self.assertEqual(self.h.cp.execute(self.h.req("cap.fs.read")).status, Status.DENIED)


if __name__ == "__main__":
    unittest.main()
