"""cycle-008 scheduler: schedules, persistence, exactly-once runs, missed-slot policies,
concurrency deferral, per-run budgets (deterministic injected clocks)."""
import calendar
import unittest

from tests.scheduler_helpers import ACTOR, SchedHarness
from scheduler import DefinitionError, JobBudget, ScheduleError, due, parse

T0 = 1_000_020  # an exact multiple of 60 is 1_000_020


class ScheduleTests(unittest.TestCase):
    def test_interval_due_and_limit(self):
        s = parse({"kind": "interval", "every_s": 60, "anchor": 0})
        self.assertEqual(due(s, 0, 59, 5), ([], 0))
        self.assertEqual(due(s, 0, 60, 5), ([60], 1))
        self.assertEqual(due(s, 60, 600, 3), ([480, 540, 600], 9))
        self.assertEqual(due(s, 600, 500, 3), ([], 0))  # backwards window

    def test_cron_fields(self):
        s = parse({"kind": "cron", "expr": "*/15 9-10 * * 1-5"})
        mon = calendar.timegm((2026, 10, 19, 9, 0, 0))  # Monday
        slots, total = due(s, mon - 1, mon + 2 * 3600, 20)
        self.assertEqual(total, 8)
        self.assertEqual(slots[0], mon)
        sat = calendar.timegm((2026, 10, 24, 9, 0, 0))
        self.assertEqual(due(s, sat - 1, sat + 3600, 5), ([], 0))
        both = parse({"kind": "cron", "expr": "0 0 1 * 0"})  # dom OR dow (classic cron)
        sun = calendar.timegm((2026, 10, 25, 0, 0, 0))
        self.assertEqual(due(both, sun - 1, sun, 1), ([sun], 1))

    def test_bad_schedules(self):
        for bad in ({"kind": "cron", "expr": "* * *"}, {"kind": "cron", "expr": "61 * * * *"},
                    {"kind": "interval", "every_s": 0}, {"kind": "interval", "every_s": True},
                    {"kind": "cron", "expr": "*/0 * * * *"}, {"kind": "interval", "every_s": 5, "x": 1}, "daily"):
            with self.assertRaises(ScheduleError):
                parse(bad)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.x = SchedHarness()
        self.x.at(T0)

    def tearDown(self):
        self.x.close()

    def test_runs_each_slot_once_through_control_plane(self):
        self.x.define("job.alpha", steps=[{"capability": "cap.job.step", "args": {}},
                                          {"capability": "cap.fs.write", "args": {"p": 1}}])
        self.assertEqual(self.x.s.tick()["ran"], [])
        self.x.at(T0 + 60)
        self.assertEqual(self.x.s.tick()["ran"], [("job.alpha", T0 + 60, "succeeded")])
        self.assertEqual(self.x.s.tick()["ran"], [])  # same slot never twice
        ops = [e["operation_id"] for e in self.x.events("execute_begin")]
        self.assertEqual(ops, [f"job:job.alpha:{T0 + 60}:0", f"job:job.alpha:{T0 + 60}:1"])
        self.assertEqual({e["actor"] for e in self.x.events("execute_begin")}, {ACTOR})

    def test_definition_persists_across_restart(self):
        self.x.define("job.persist")
        s2 = self.x.new(owner="sched-2")
        self.x.at(T0 + 60)
        self.assertEqual(s2.tick()["ran"], [("job.persist", T0 + 60, "succeeded")])

    def test_missed_skip_runs_latest_only_and_records_gap(self):
        self.x.define("job.skip")
        self.x.at(T0 + 600)
        res = self.x.s.tick()
        self.assertEqual(res["ran"], [("job.skip", T0 + 600, "succeeded")])
        self.assertEqual(self.x.s.store.gaps("job.skip"), [{"after": T0, "upto": T0 + 599, "skipped": 9,
                                                            "policy": "skip"}])

    def test_missed_catch_up_is_capped(self):
        self.x.define("job.catch", missed="catch_up", catch_up_max=3)
        self.x.at(T0 + 600)
        res = self.x.s.tick()
        self.assertEqual([r[1] for r in res["ran"]], [T0 + 480, T0 + 540, T0 + 600])
        self.assertEqual(self.x.s.store.gaps("job.catch")[0]["skipped"], 7)
        self.assertEqual(self.x.s.tick()["ran"], [])

    def test_concurrency_limit_defers_not_drops(self):
        x = self.x
        s = x.new(max_concurrent=1)
        x.define("job.a")
        x.define("job.b")
        s.store.claim("job.zzz", 1, "other-instance", T0, T0 + 10_000)  # a live lease held elsewhere
        x.at(T0 + 60)
        res = s.tick()
        self.assertEqual((res["ran"], res["deferred"]), ([], 2))
        s.store.finish("job.zzz", 1, "other-instance", "succeeded", "", T0 + 60)
        res = s.tick()
        self.assertEqual([r[0] for r in res["ran"]], ["job.a", "job.b"])  # deferred slots still run

    def test_run_budget_wall_clock(self):
        x = self.x
        x.advance_mono = 100.0
        x.define("job.slow", steps=[{"capability": "cap.job.slow", "args": {}},
                                    {"capability": "cap.job.step", "args": {}}],
                 budget=JobBudget(max_steps=5, max_wall_s=50.0))
        x.at(T0 + 60)
        run = x.s.tick()["ran"][0]
        self.assertEqual(run[2], "budget_exhausted")
        self.assertEqual(x.calls["cap.job.step"], 0)
        self.assertEqual(x.s.store.runs("job.slow")[0]["reason"], "wall_clock_budget_exhausted")

    def test_run_budget_cost_and_steps(self):
        with self.assertRaises(DefinitionError) as e:
            self.x.define("job.many", steps=[{"capability": "cap.job.step", "args": {}}] * 6)
        self.assertEqual(e.exception.code, "steps_exceed_budget")
        self.x.define("job.cost", steps=[{"capability": "cap.job.step", "args": {}, "cost": 2.0}],
                      budget=JobBudget(max_cost=1.0))
        self.x.at(T0 + 60)
        self.assertEqual(self.x.s.tick()["ran"][0][2], "budget_exhausted")


if __name__ == "__main__":
    unittest.main()
