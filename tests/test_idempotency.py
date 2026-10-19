import threading
import unittest

from tests.helpers import Harness
from control_plane import ControlPlane, IdempotencyLedger, Status


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.fs.write")

    def tearDown(self):
        self.h.close()

    def test_replay_returns_same_result_without_reexecuting(self):
        first = self.h.cp.execute(self.h.req("cap.fs.write"))
        second = self.h.cp.execute(self.h.req("cap.fs.write"))
        self.assertEqual(first.status, Status.EXECUTED)
        self.assertEqual(second.status, Status.REPLAYED)
        self.assertEqual(first.result, second.result)
        self.assertEqual(self.h.calls["cap.fs.write"], 1)

    def test_replay_consumes_no_budget_and_needs_no_new_approval(self):
        self.h.policy.grant("agent.alpha", "cap.payment.transfer")
        tok = self.h.approve("cap.payment.transfer")
        self.h.cp.execute(self.h.req("cap.payment.transfer", token=tok))
        spent_iters = self.h.governor.iterations
        out = self.h.cp.execute(self.h.req("cap.payment.transfer"))  # no token on replay
        self.assertEqual(out.status, Status.REPLAYED)
        self.assertEqual(self.h.governor.iterations, spent_iters)
        self.assertEqual(self.h.calls["cap.payment.transfer"], 1)

    def test_replay_survives_restart(self):
        self.h.cp.execute(self.h.req("cap.fs.write"))
        self.h.ledger.close()
        ledger2 = IdempotencyLedger(self.h.ledger_path)
        cp2 = ControlPlane(policy=self.h.policy, approvals=self.h.approvals, ledger=ledger2,
                           audit=self.h.audit, kill_switch=self.h.kill, governor=self.h.governor)
        cp2.bind("cap.fs.write", self.h._counter("cap.fs.write"))
        out = cp2.execute(self.h.req("cap.fs.write"))
        self.assertEqual(out.status, Status.REPLAYED)
        self.assertEqual(self.h.calls["cap.fs.write"], 1)
        self.h.ledger = ledger2

    def test_reused_operation_id_with_different_args_conflicts(self):
        self.h.cp.execute(self.h.req("cap.fs.write", args={"x": 1}))
        out = self.h.cp.execute(self.h.req("cap.fs.write", args={"x": 2}))
        self.assertEqual(out.status, Status.CONFLICT)
        self.assertEqual(self.h.calls["cap.fs.write"], 1)

    def test_crash_mid_operation_is_in_doubt_not_reexecuted(self):
        # Simulate a crash: the op was claimed (PENDING) but never finished.
        from control_plane import args_sha256
        self.h.ledger.begin("op-00000001", "cap.fs.write", "agent.alpha", args_sha256({"x": 1}))
        out = self.h.cp.execute(self.h.req("cap.fs.write"))
        self.assertEqual(out.status, Status.IN_DOUBT)
        self.assertEqual(self.h.total_calls(), 0)

    def test_concurrent_duplicates_execute_once(self):
        results = []

        def go():
            results.append(self.h.cp.execute(self.h.req("cap.fs.write", op="op-concurrent")).status)
        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.h.calls["cap.fs.write"], 1)
        self.assertEqual(results.count(Status.EXECUTED), 1)
        self.assertTrue(set(results) <= {Status.EXECUTED, Status.REPLAYED, Status.IN_DOUBT})


if __name__ == "__main__":
    unittest.main()
