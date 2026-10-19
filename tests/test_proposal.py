import unittest

from tests.helpers import Harness  # noqa: F401  (sets sys.path)
from orchestrator import ActionProposal, FinalProposal, ProposalError, parse_proposal


class ProposalParserTests(unittest.TestCase):
    def test_valid_action_and_final(self):
        a = parse_proposal('{"type":"action","capability":"cap.fs.read","args":{"p":"x"},"idempotency_key":"k1"}')
        self.assertEqual(a, ActionProposal("cap.fs.read", {"p": "x"}, "k1", ""))
        self.assertEqual(parse_proposal('{"type":"final","answer":"done"}'), FinalProposal("done"))

    def test_single_json_fence_tolerated(self):
        self.assertEqual(parse_proposal('```json\n{"type":"final","answer":"ok"}\n```'), FinalProposal("ok"))

    def test_malformed_outputs_have_stable_codes(self):
        cases = {
            "output_not_text": None,
            "output_too_large": '{"type":"final","answer":"' + "a" * 20000 + '"}',
            "not_a_json_object": "Sure! I'll send the email now.",
            "invalid_json": '{"type":"final","answer":"x"',
            "duplicate_key": '{"type":"action","capability":"cap.fs.read","capability":"cap.payment.transfer","args":{}}',
            "non_finite_number": '{"type":"action","capability":"cap.fs.read","args":{"n":NaN}}',
            "bad_fields": '{"type":"action","capability":"cap.fs.read","args":{},"approval_token":"x"}',
            "bad_capability": '{"type":"action","capability":"cap.fs.read\\n","args":{}}',
            "args_not_object": '{"type":"action","capability":"cap.fs.read","args":"rm -rf /"}',
            "bad_idempotency_key": '{"type":"action","capability":"cap.fs.read","args":{},"idempotency_key":"../x"}',
            "bad_answer": '{"type":"final","answer":42}',
            "unknown_type": '{"type":"handler","name":"cap.fs.write"}',
            "too_deep": '{"type":"action","capability":"cap.fs.read","args":' + '{"a":' * 20 + '1' + '}' * 20 + '}',
        }
        for code, text in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(ProposalError) as cm:
                    parse_proposal(text)
                self.assertEqual(cm.exception.code, code)

    def test_two_objects_rejected(self):
        with self.assertRaises(ProposalError):
            parse_proposal('{"type":"final","answer":"a"}{"type":"final","answer":"b"}')

    def test_extreme_nesting_does_not_crash(self):
        with self.assertRaises(ProposalError):
            parse_proposal("{" + '"a":[' * 5000 + "]" * 5000 + "}")


if __name__ == "__main__":
    unittest.main()
