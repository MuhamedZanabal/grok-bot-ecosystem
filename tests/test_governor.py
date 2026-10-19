import unittest

from tests.helpers import Harness
from control_plane import Budget, LoopGovernor, LoopState, Status


class GovernorTests(unittest.TestCase):
    def tearDown(self):
        if hasattr(self, "h"):
            self.h.close()

    def _run_until_stop(self, budget, cost=0.0, advance=0.0, n=50):
        self.h = Harness(budget=budget)
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        last = None
        for i in range(n):
            last = self.h.cp.execute(self.h.req("cap.fs.read", op=f"op-iter-{i:04d}"), cost=cost)
            self.h.mono.t += advance
            if last.status is not Status.EXECUTED:
                break
        return last

    def test_iteration_budget_aborts(self):
        out = self._run_until_stop(Budget(3, 3600, 100))
        self.assertEqual((out.status, out.reason), (Status.CYCLE_ABORTED, "iteration_budget_exhausted"))
        self.assertEqual(self.h.calls["cap.fs.read"], 3)

    def test_cost_budget_aborts_before_overspend(self):
        out = self._run_until_stop(Budget(100, 3600, 1.0), cost=0.4)
        self.assertEqual((out.status, out.reason), (Status.CYCLE_ABORTED, "cost_budget_exhausted"))
        self.assertEqual(self.h.calls["cap.fs.read"], 2)
        self.assertLessEqual(self.h.governor.spent, 1.0)

    def test_wall_clock_budget_aborts(self):
        out = self._run_until_stop(Budget(100, 10, 100), advance=4)
        self.assertEqual((out.status, out.reason), (Status.CYCLE_ABORTED, "wall_clock_budget_exhausted"))
        self.assertEqual(self.h.calls["cap.fs.read"], 3)

    def test_abort_is_sticky(self):
        self._run_until_stop(Budget(1, 3600, 100))
        self.h.governor.budget = Budget(1000, 3600, 100)  # raising the limit afterwards does not revive
        out = self.h.cp.execute(self.h.req("cap.fs.read", op="op-after-abort"))
        self.assertEqual(out.status, Status.CYCLE_ABORTED)
        self.assertEqual(self.h.governor.state, LoopState.CYCLE_ABORTED)

    def test_invalid_cost_and_budget_rejected(self):
        self.h = Harness()
        self.h.policy.grant("agent.alpha", "cap.fs.read")
        for bad in (-1, float("nan"), float("inf")):
            out = self.h.cp.execute(self.h.req("cap.fs.read", op="op-badcost"), cost=bad)
            self.assertEqual((out.status, out.reason), (Status.REJECTED, "bad_cost"))
        self.assertEqual(self.h.total_calls(), 0)
        for bad in (Budget, ):
            with self.assertRaises(ValueError):
                bad(-1, 10, 10)
            with self.assertRaises(ValueError):
                bad(1, float("inf"), 10)

    def test_complete_state(self):
        g = LoopGovernor(Budget(1, 1, 1))
        self.assertEqual(g.complete(), LoopState.COMPLETED)
        self.assertEqual(g.admit(), LoopState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
