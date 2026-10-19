import unittest

from tests.helpers import Harness, KEY
from control_plane import ApprovalAuthority, Status


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.email.send")
        self.h.policy.grant("agent.alpha", "cap.payment.transfer")
        self.h.policy.grant("agent.beta", "cap.email.send")

    def tearDown(self):
        self.h.close()

    def test_level2_and_level3_require_approval(self):
        for cap in ("cap.email.send", "cap.payment.transfer"):
            out = self.h.cp.execute(self.h.req(cap, op=f"op-{cap}"))
            self.assertEqual(out.status, Status.APPROVAL_REQUIRED)
        self.assertEqual(self.h.total_calls(), 0)

    def test_valid_approval_executes(self):
        tok = self.h.approve("cap.payment.transfer")
        out = self.h.cp.execute(self.h.req("cap.payment.transfer", token=tok))
        self.assertEqual(out.status, Status.EXECUTED)
        self.assertEqual(self.h.calls["cap.payment.transfer"], 1)

    def _assert_invalid(self, request, code):
        out = self.h.cp.execute(request)
        self.assertEqual((out.status, out.reason), (Status.APPROVAL_INVALID, code))
        self.assertEqual(self.h.total_calls(), 0)

    def test_arg_tampering_rejected(self):
        tok = self.h.approve("cap.payment.transfer", args={"amount": 10, "to": "a"})
        self._assert_invalid(self.h.req("cap.payment.transfer", args={"amount": 10000, "to": "a"}, token=tok),
                             "approval_args_mismatch")

    def test_expired_rejected(self):
        tok = self.h.approve("cap.email.send", ttl=60)
        self.h.wall.t += 60
        self._assert_invalid(self.h.req("cap.email.send", token=tok), "approval_expired")

    def test_wrong_capability_scope_rejected(self):
        tok = self.h.approve("cap.email.send")
        self._assert_invalid(self.h.req("cap.payment.transfer", token=tok), "approval_wrong_scope")

    def test_wrong_operation_scope_rejected(self):
        tok = self.h.approve("cap.email.send", op="op-00000001")
        self._assert_invalid(self.h.req("cap.email.send", op="op-00000002", token=tok), "approval_wrong_scope")

    def test_wrong_actor_scope_rejected(self):
        tok = self.h.approve("cap.email.send", actor="agent.alpha")
        self._assert_invalid(self.h.req("cap.email.send", actor="agent.beta", token=tok), "approval_wrong_scope")

    def test_forged_signature_rejected(self):
        tok = self.h.approve("cap.email.send")
        body, sig = tok.split(".")
        self._assert_invalid(self.h.req("cap.email.send", token=body + "." + "0" * len(sig)),
                             "approval_bad_signature")

    def test_token_from_other_key_rejected(self):
        other = ApprovalAuthority(b"z" * 32, clock=self.h.wall)
        tok = other.issue(actor="agent.alpha", capability="cap.email.send", operation_id="op-00000001",
                          args={"x": 1}, ttl_seconds=60, approver="mallory")
        self._assert_invalid(self.h.req("cap.email.send", token=tok), "approval_bad_signature")

    def test_garbage_token_rejected(self):
        for junk in ("", "abc", "a.b.c", "!!!.zz"):
            self._assert_invalid(self.h.req("cap.email.send", token=junk), "approval_malformed"
                                 if junk != "!!!.zz" else "approval_bad_signature")

    def test_approval_executes_once(self):
        tok = self.h.approve("cap.payment.transfer")
        first = self.h.cp.execute(self.h.req("cap.payment.transfer", token=tok))
        second = self.h.cp.execute(self.h.req("cap.payment.transfer", token=tok))
        self.assertEqual((first.status, second.status), (Status.EXECUTED, Status.REPLAYED))
        self.assertEqual(self.h.calls["cap.payment.transfer"], 1)

    def test_authority_guards(self):
        with self.assertRaises(ValueError):
            ApprovalAuthority(b"short")
        a = ApprovalAuthority(KEY)
        self.assertNotIn("k" * 32, repr(a))
        with self.assertRaises(ValueError):
            a.issue(actor="a", capability="cap.x.y", operation_id="op-00000001", args={},
                    ttl_seconds=86400, approver="z")


if __name__ == "__main__":
    unittest.main()
