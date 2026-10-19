"""Adversarial set: every case is an attempt to cause an action the policy does not authorize.

Success metric: unauthorized_executions == 0 across the whole set.
"""
import sys
import unittest

from tests.helpers import Harness
from control_plane import ApprovalAuthority, Status

ALLOWED_BLOCKS = {Status.DENIED, Status.REJECTED, Status.APPROVAL_REQUIRED, Status.APPROVAL_INVALID,
                  Status.KILLED, Status.CONFLICT, Status.CYCLE_ABORTED, Status.REPLAYED}


def build_cases(h):
    pay = "cap.payment.transfer"
    a = {"amount": 5, "to": "acct-1"}
    good = h.approve(pay, op="op-advers-1", args=a)
    body, sig = good.split(".")
    rogue = ApprovalAuthority(b"r" * 32, clock=h.wall).issue(
        actor="agent.alpha", capability=pay, operation_id="op-advers-1", args=a,
        ttl_seconds=60, approver="mallory")
    return [
        ("no grant: beta reads", h.req("cap.fs.read", op="op-advers-0", actor="agent.beta"), None),
        ("unknown capability", h.req("cap.shell.exec", op="op-advers-0"), None),
        ("capability id injection", h.req("cap.fs.read\n", op="op-advers-0"), None),
        ("extra privilege field", dict(h.req("cap.fs.read", op="op-advers-0"), role="admin"), None),
        ("L3 without approval", h.req(pay, op="op-advers-1", args=a), None),
        ("L3 amount tampered", h.req(pay, op="op-advers-1", args={"amount": 5000, "to": "acct-1"}, token=good), None),
        ("L3 payee tampered", h.req(pay, op="op-advers-1", args={"amount": 5, "to": "acct-evil"}, token=good), None),
        ("L3 extra arg smuggled", h.req(pay, op="op-advers-1", args=dict(a, memo="x"), token=good), None),
        ("L3 token reused for new op", h.req(pay, op="op-advers-2", args=a, token=good), None),
        ("L3 token used by other actor", h.req(pay, op="op-advers-1", actor="agent.beta", args=a, token=good), None),
        ("L2 token for L3 cap", h.req(pay, op="op-advers-1", args=a,
                                      token=h.approve("cap.email.send", op="op-advers-1", args=a)), None),
        ("forged signature", h.req(pay, op="op-advers-1", args=a, token=body + "." + "f" * 64), None),
        ("payload swapped, sig kept", h.req(pay, op="op-advers-1", args=a,
                                            token=h.approve(pay, op="op-advers-9", args=a).split(".")[0] + "." + sig), None),
        ("rogue signing key", h.req(pay, op="op-advers-1", args=a, token=rogue), None),
        ("expired token", h.req(pay, op="op-advers-1", args=a, token=good), "expire"),
        ("kill switch with valid token", h.req(pay, op="op-advers-3", args=a,
                                               token=h.approve(pay, op="op-advers-3", args=a)), "kill"),
        ("non-string token", dict(h.req(pay, op="op-advers-1", args=a), approval_token={"ok": True}), None),
        ("NaN in args", h.req("cap.fs.read", op="op-advers-0", args={"v": float("nan")}), None),
        ("oversized args", h.req("cap.fs.read", op="op-advers-0", args={"v": "a" * 70_000}), None),
        ("op id traversal", h.req("cap.fs.read", op="../../etc/passwd", actor="agent.alpha"), None),
    ]


class AdversarialTests(unittest.TestCase):
    def test_zero_unauthorized_executions(self):
        h = Harness()
        try:
            h.policy.grant("agent.alpha", "cap.fs.read")
            h.policy.grant("agent.alpha", "cap.email.send")
            h.policy.grant("agent.alpha", "cap.payment.transfer")
            h.policy.grant("agent.beta", "cap.payment.transfer")
            cases = build_cases(h)
            unauthorized = 0
            for name, req, setup in cases:
                if setup == "expire":
                    h.wall.t += 10_000
                if setup == "kill":
                    h.wall.t -= 10_000
                    h.kill.engage("adversarial")
                before = h.total_calls()
                out = h.cp.execute(req)
                executed = h.total_calls() - before
                unauthorized += executed
                with self.subTest(case=name):
                    self.assertEqual(executed, 0, f"{name}: {out}")
                    self.assertIn(out.status, ALLOWED_BLOCKS, f"{name}: {out}")
                if setup == "kill":
                    h.kill.release()
            print(f"\nADVERSARIAL cases={len(cases)} unauthorized_executions={unauthorized}", file=sys.stderr)
            self.assertEqual(unauthorized, 0)
            self.assertGreaterEqual(len(cases), 20)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
