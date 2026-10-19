import json
import unittest

from tests.helpers import Harness, act, final, make_runner
from control_plane import Status
from control_plane.executor import Status as S
from orchestrator import AgentRunner, MockProvider, ProviderTimeout, ProviderUnavailable, RunState
from orchestrator import runner as runner_mod


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        for cap in ("cap.fs.read", "cap.fs.write", "cap.email.send"):
            self.h.policy.grant("agent.alpha", cap)

    def tearDown(self):
        self.h.close()

    def test_happy_path_executes_through_control_plane(self):
        r, (p,) = make_runner(self.h, [act("cap.fs.read", {"path": "a"}), final("read it")])
        res = r.run("read a", "run-1")
        self.assertEqual((res.state, res.answer), (RunState.COMPLETED, "read it"))
        self.assertEqual(self.h.calls, {"cap.fs.read": 1})
        second_request = json.loads(p.calls[1])
        self.assertEqual(second_request["transcript"][-1]["status"], "executed")

    def test_duplicate_proposals_execute_once(self):
        r, _ = make_runner(self.h, [act("cap.fs.write", {"x": 1})] * 4 + [final()])
        res = r.run("write", "run-dup")
        self.assertEqual(res.state, RunState.COMPLETED)
        self.assertEqual(self.h.calls["cap.fs.write"], 1)
        self.assertEqual([s["status"] for s in res.steps if s["kind"] == "action"],
                         ["executed", "replayed", "replayed", "replayed"])

    def test_distinct_idempotency_keys_are_distinct_operations(self):
        r, _ = make_runner(self.h, [act("cap.fs.write", {"x": 1}, idempotency_key="a"),
                                    act("cap.fs.write", {"x": 1}, idempotency_key="b"), final()])
        r.run("write twice", "run-keys")
        self.assertEqual(self.h.calls["cap.fs.write"], 2)

    def test_rerun_same_run_id_replays_instead_of_reexecuting(self):
        script = [act("cap.fs.write", {"x": 1}), final()]
        make_runner(self.h, list(script))[0].run("w", "run-same")
        make_runner(self.h, list(script))[0].run("w", "run-same")
        self.assertEqual(self.h.calls["cap.fs.write"], 1)

    def test_malformed_output_recovers_then_completes(self):
        r, (p,) = make_runner(self.h, ["not json", final("ok")])
        res = r.run("t", "run-mal-1")
        self.assertEqual(res.state, RunState.COMPLETED)
        self.assertEqual(json.loads(p.calls[1])["transcript"][-1],
                         {"role": "observation", "status": "malformed_output", "reason": "not_a_json_object"})

    def test_malformed_output_limit_aborts(self):
        r, _ = make_runner(self.h, ["x", "{", '{"type":"nope"}', final()], max_malformed=2)
        res = r.run("t", "run-mal-2")
        self.assertEqual((res.state, res.reason), (RunState.MALFORMED_OUTPUT, "unknown_type"))
        self.assertEqual(self.h.total_calls(), 0)

    def test_provider_timeout_falls_back_to_next_provider(self):
        slow = MockProvider([("sleep", 0.5, final("late"))] * 2, name="slow")
        backup = MockProvider([final("from backup")], name="backup")
        r, _ = make_runner(self.h, slow, backup, provider_timeout_s=0.1)
        res = r.run("t", "run-to")
        self.assertEqual((res.state, res.answer), (RunState.COMPLETED, "from backup"))
        self.assertEqual(len(slow.calls), 2)

    def test_retry_then_success_on_same_provider(self):
        r, (p,) = make_runner(self.h, [ProviderUnavailable("503"), final("second try")])
        res = r.run("t", "run-retry")
        self.assertEqual((res.state, res.answer), (RunState.COMPLETED, "second try"))

    def test_total_outage_is_provider_failed(self):
        a = MockProvider([ProviderUnavailable()] * 2, name="a")
        b = MockProvider([ProviderTimeout(), RuntimeError("boom")], name="b")
        r, _ = make_runner(self.h, a, b)
        res = r.run("t", "run-outage")
        self.assertEqual((res.state, res.reason), (RunState.PROVIDER_FAILED, "provider_error:RuntimeError"))
        self.assertEqual(self.h.total_calls(), 0)

    def test_model_call_budget_aborts(self):
        r, _ = make_runner(self.h, [act("cap.fs.read", {"i": i}) for i in range(10)], max_model_calls=3)
        res = r.run("loop forever", "run-budget")
        self.assertEqual((res.state, res.reason), (RunState.CYCLE_ABORTED, "iteration_budget_exhausted"))
        self.assertEqual(self.h.calls["cap.fs.read"], 3)

    def test_model_cost_budget_aborts(self):
        r, _ = make_runner(self.h, [act("cap.fs.read", {"i": i}) for i in range(10)],
                           max_model_cost=0.25, model_call_cost=0.1)
        res = r.run("t", "run-cost")
        self.assertEqual((res.state, res.reason), (RunState.CYCLE_ABORTED, "cost_budget_exhausted"))

    def test_kill_switch_stops_run(self):
        self.h.kill.engage()
        r, (p,) = make_runner(self.h, [final()])
        self.assertEqual(r.run("t", "run-kill").state, RunState.KILLED)
        self.assertEqual(p.calls, [])

    def test_approval_required_pauses_and_resume_completes(self):
        script = [act("cap.fs.read", {"q": 1}), act("cap.email.send", {"to": "a@b.c", "body": "hi"}), final("sent")]
        r1, _ = make_runner(self.h, list(script))
        res1 = r1.run("send", "run-appr")
        self.assertEqual(res1.state, RunState.AWAITING_APPROVAL)
        self.assertNotIn("cap.email.send", self.h.calls)
        pend = res1.pending
        token = self.h.approvals.issue(actor=pend.actor, capability=pend.capability,
                                       operation_id=pend.operation_id, args=pend.args,
                                       ttl_seconds=300, approver="zanabal")
        r2, _ = make_runner(self.h, list(script))
        res2 = r2.run("send", "run-appr", approvals={pend.operation_id: token})
        self.assertEqual((res2.state, res2.answer), (RunState.COMPLETED, "sent"))
        self.assertEqual(self.h.calls, {"cap.fs.read": 1, "cap.email.send": 1})

    def test_blocked_limit(self):
        r, _ = make_runner(self.h, [act("cap.payment.transfer", {"i": i}) for i in range(10)], max_blocked=2)
        res = r.run("t", "run-blocked")
        self.assertEqual((res.state, res.reason), (RunState.BLOCKED_LIMIT, "deny:no_grant"))

    def test_in_doubt_stops_run(self):
        from control_plane import args_sha256
        from orchestrator import ActionProposal, operation_id_for
        op = operation_id_for("run-doubt", ActionProposal("cap.fs.write", {"x": 1}))
        self.h.ledger.begin(op, "cap.fs.write", "agent.alpha", args_sha256({"x": 1}))
        r, _ = make_runner(self.h, [act("cap.fs.write", {"x": 1}), final()])
        self.assertEqual(r.run("t", "run-doubt").state, RunState.IN_DOUBT)
        self.assertEqual(self.h.total_calls(), 0)

    def test_large_results_are_truncated_in_transcript(self):
        self.h.cp.bind("cap.fs.read", lambda a: {"blob": "z" * 10_000})
        r, (p,) = make_runner(self.h, [act("cap.fs.read"), final()], max_observation_bytes=100)
        r.run("t", "run-trunc")
        obs = json.loads(p.calls[1])["transcript"][-1]
        self.assertTrue(obs["truncated"])
        self.assertLessEqual(len(obs["result"]), 100)

    def test_every_status_is_mapped(self):
        mapped = (runner_mod._CONTINUE | runner_mod._BLOCKED | set(runner_mod._TERMINAL)
                  | {S.APPROVAL_REQUIRED})
        self.assertEqual(mapped, set(Status))

    def test_input_validation(self):
        r, _ = make_runner(self.h, [final()])
        with self.assertRaises(ValueError):
            r.run("t", "bad run id!")
        with self.assertRaises(ValueError):
            r.run("", "run-x")
        with self.assertRaises(ValueError):
            AgentRunner(self.h.cp.gateway("agent.alpha"), [], self.h.audit)

    def test_run_is_audited_without_raw_output(self):
        r, _ = make_runner(self.h, [act("cap.fs.read", {"q": "SECRET-ARG-VALUE"}), final("ANSWER-TEXT")])
        r.run("t", "run-audit")
        with open(self.h.audit_path) as fh:
            raw = fh.read()
            events = [json.loads(line)["event"] for line in raw.splitlines()]
        for e in ("run_start", "model_call", "proposal", "decision", "run_end"):
            self.assertIn(e, events)
        self.assertNotIn("ANSWER-TEXT", raw)  # model output stored as hash + length only


if __name__ == "__main__":
    unittest.main()
