import time
import unittest

from tests.helpers import Harness
from control_plane import RateLimiter, Status


class Odd:
    pass


class FailureModeTests(unittest.TestCase):
    def tearDown(self):
        self.h.close()

    def test_malformed_requests_rejected(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        good = self.h.req("cap.fs.read")
        cases = {
            "request_not_mapping": ["not", "a", "dict"],
            "missing_fields:args": {k: v for k, v in good.items() if k != "args"},
            "unknown_fields:sudo": dict(good, sudo=True),
            "bad_operation_id": dict(good, operation_id="short"),
            "bad_actor": dict(good, actor="Agent Alpha!"),
            "bad_capability": dict(good, capability="cap.fs.read;rm -rf"),
            "args_not_object": dict(good, args=[1, 2]),
            "args_not_canonical_json": dict(good, args={"x": float("nan")}),
            "args_too_large": dict(good, args={"x": "a" * 70_000}),
            "bad_approval_token_type": dict(good, approval_token=123),
        }
        for reason, req in cases.items():
            out = self.h.cp.execute(req)
            self.assertEqual((out.status, out.reason), (Status.REJECTED, reason), reason)
        out = self.h.cp.execute(dict(good, args={"o": Odd()}))
        self.assertEqual((out.status, out.reason), (Status.REJECTED, "args_not_canonical_json"))
        self.assertEqual(self.h.total_calls(), 0)

    def test_handler_exception_is_failed_and_terminal(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.fs.write")
        n = {"c": 0}

        def boom(args):
            n["c"] += 1
            raise RuntimeError("secret ghp_" + "Z" * 36)
        self.h.cp.bind("cap.fs.write", boom)
        first = self.h.cp.execute(self.h.req("cap.fs.write"))
        second = self.h.cp.execute(self.h.req("cap.fs.write"))
        self.assertEqual((first.status, first.reason), (Status.FAILED, "handler_error:RuntimeError"))
        self.assertEqual(second.status, Status.FAILED)
        self.assertEqual(n["c"], 1)
        with open(self.h.audit_path) as fh:
            self.assertNotIn("Z" * 36, fh.read())

    def test_handler_timeout_is_terminal(self):
        self.h = Harness(timeout=0.2)
        self.h.policy.grant("agent.alpha", "cap.fs.write")
        self.h.cp.bind("cap.fs.write", lambda args: time.sleep(1.0))
        first = self.h.cp.execute(self.h.req("cap.fs.write"))
        second = self.h.cp.execute(self.h.req("cap.fs.write"))
        self.assertEqual((first.status, first.reason), (Status.TIMED_OUT, "handler_timeout"))
        self.assertEqual(second.status, Status.TIMED_OUT)

    def test_non_json_result_is_failed(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        self.h.cp.bind("cap.fs.read", lambda args: Odd())
        out = self.h.cp.execute(self.h.req("cap.fs.read"))
        self.assertEqual((out.status, out.reason), (Status.FAILED, "handler_result_not_json"))

    def test_handler_cannot_mutate_callers_args(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.fs.read")

        def mutate(args):
            args["x"] = "mutated"
            return {}
        self.h.cp.bind("cap.fs.read", mutate)
        req = self.h.req("cap.fs.read")
        self.h.cp.execute(req)
        self.assertEqual(req["args"], {"x": 1})

    def test_rate_limit(self):
        self.h = Harness(rate=RateLimiter(0.0, 1, overrides={"cap.fs.read": (0.0, 2)}))
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        statuses = [self.h.cp.execute(self.h.req("cap.fs.read", op=f"op-rl-{i:04d}")).status for i in range(4)]
        self.assertEqual(statuses, [Status.EXECUTED, Status.EXECUTED, Status.RATE_LIMITED, Status.RATE_LIMITED])
        # rate-limited ops are not recorded, so they may be retried later
        self.assertIsNone(self.h.ledger.get("op-rl-0002"))

    def test_rate_limiter_refills(self):
        self.h = Harness()
        from tests.helpers import Clock
        c = Clock(0.0)
        rl = RateLimiter(1.0, 1, clock=c)
        self.assertEqual([rl.allow("k"), rl.allow("k")], [True, False])
        c.t += 1.0
        self.assertTrue(rl.allow("k"))


if __name__ == "__main__":
    unittest.main()
