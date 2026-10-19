import unittest

from tests.helpers import Harness
from control_plane import Capability, RiskLevel, Status
from control_plane.schema import ArgsInvalid, SchemaError, check_schema, validate

S = {"type": "object", "required": ["to", "amount"],
     "properties": {"to": {"type": "string", "pattern": "[a-z0-9-]{3,20}", "maxLength": 20},
                    "amount": {"type": "integer", "minimum": 1, "maximum": 100},
                    "currency": {"type": "string", "enum": ["USD", "BHD"]},
                    "tags": {"type": "array", "items": {"type": "string", "maxLength": 8}, "maxItems": 3},
                    "meta": {"type": "object", "properties": {"note": {"type": "string"}}}}}


class SchemaValidatorTests(unittest.TestCase):
    def ok(self, v):
        validate(S, v)

    def bad(self, v, code):
        with self.assertRaises(ArgsInvalid) as cm:
            validate(S, v)
        self.assertEqual(cm.exception.code, code)

    def test_valid(self):
        self.ok({"to": "acct-1", "amount": 5, "currency": "BHD", "tags": ["a"], "meta": {"note": "x"}})

    def test_violations_have_stable_codes(self):
        self.bad({"amount": 5}, "$.to:required")
        self.bad({"to": "acct-1", "amount": 5, "sudo": True}, "$.sudo:additional_property")
        self.bad({"to": "acct-1", "amount": True}, "$.amount:type_integer")
        self.bad({"to": "acct-1", "amount": 1.5}, "$.amount:type_integer")
        self.bad({"to": "acct-1", "amount": 101}, "$.amount:maximum")
        self.bad({"to": "acct-1", "amount": 0}, "$.amount:minimum")
        self.bad({"to": "acct-1\n", "amount": 5}, "$.to:pattern")
        self.bad({"to": "evil acct", "amount": 5}, "$.to:pattern")
        self.bad({"to": "acct-1", "amount": 5, "currency": "EUR"}, "$.currency:enum")
        self.bad({"to": "acct-1", "amount": 5, "tags": ["a", "b", "c", "d"]}, "$.tags:maxItems")
        self.bad({"to": "acct-1", "amount": 5, "tags": ["waytoolongtag"]}, "$.tags[0]:maxLength")
        self.bad({"to": "acct-1", "amount": 5, "meta": {"note": "x", "inject": 1}}, "$.meta.inject:additional_property")
        self.bad({"to": "acct-1", "amount": 5, "meta": "x"}, "$.meta:type_object")

    def test_number_rejects_bool_and_nonfinite(self):
        n = {"type": "number"}
        for v in (True, float("nan"), float("inf")):
            with self.assertRaises(ArgsInvalid):
                validate(n, v)
        validate(n, 2.5)

    def test_unsupported_or_bad_schemas_fail_closed(self):
        for bad in ({"type": "object", "$ref": "#/x"}, {"type": "object", "oneOf": []},
                    {"type": "string", "format": "email"}, {"properties": {}}, {"type": "blob"},
                    {"type": "object", "additionalProperties": {"type": "string"}},
                    {"type": "object", "required": ["missing"]}, {"type": "string", "pattern": "("},
                    {"type": "string", "maxLength": -1}, {"type": "number", "minimum": float("nan")},
                    "not a schema"):
            with self.subTest(schema=bad):
                with self.assertRaises(SchemaError):
                    check_schema(bad)

    def test_schema_too_deep(self):
        s = {"type": "object"}
        for _ in range(12):
            s = {"type": "object", "properties": {"a": s}}
        with self.assertRaises(SchemaError):
            check_schema(s)

    def test_annotations_allowed(self):
        check_schema({"type": "object", "title": "t", "description": "d", "$schema": "x",
                      "properties": {"a": {"type": "string", "default": "z", "examples": ["q"]}}})


class SchemaEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.h.policy.register(Capability("cap.pay.small", RiskLevel.LOCAL_WRITE, "", S))
        self.n = 0

        def handler(args):
            self.n += 1
            return {}
        self.h.cp.bind("cap.pay.small", handler)
        self.h.policy.grant("agent.alpha", "cap.pay.small")

    def tearDown(self):
        self.h.close()

    def test_invalid_args_rejected_before_execution(self):
        out = self.h.cp.execute(self.h.req("cap.pay.small", args={"to": "acct-1", "amount": 1000}))
        self.assertEqual((out.status, out.reason), (Status.REJECTED, "schema:$.amount:maximum"))
        self.assertEqual(self.n, 0)
        self.assertIsNone(self.h.ledger.get("op-00000001"))  # nothing claimed, retry allowed

    def test_valid_args_execute(self):
        out = self.h.cp.execute(self.h.req("cap.pay.small", args={"to": "acct-1", "amount": 10}))
        self.assertEqual(out.status, Status.EXECUTED)

    def test_schema_is_immutable_after_registration(self):
        cap = self.h.policy.capability("cap.pay.small")
        cap.schema["properties"]["amount"]["maximum"] = 10**9  # mutating a copy
        out = self.h.cp.execute(self.h.req("cap.pay.small", args={"to": "acct-1", "amount": 1000}))
        self.assertEqual(out.status, Status.REJECTED)
        with self.assertRaises(Exception):
            cap.schema_json = "{}"

    def test_schema_json_constructor_path_is_revalidated(self):
        with self.assertRaises(SchemaError):
            Capability("cap.x.y", RiskLevel.READ, "", schema_json='{"type":"object","$ref":"#"}')

    def test_bind_requires_schema(self):
        self.h.policy.register(Capability("cap.noschema.x", RiskLevel.READ))
        with self.assertRaises(ValueError):
            self.h.cp.bind("cap.noschema.x", lambda a: {})

    def test_default_control_plane_is_strict(self):
        from control_plane import ControlPlane
        cp = ControlPlane(policy=self.h.policy, approvals=self.h.approvals, ledger=self.h.ledger,
                          audit=self.h.audit, kill_switch=self.h.kill, governor=self.h.governor)
        with self.assertRaises(ValueError):
            cp.bind("cap.pay.small", lambda a: {})

    def test_strict_control_plane_refuses_inprocess_handlers(self):
        strict = Harness(inprocess=False)
        try:
            with self.assertRaises(ValueError):
                strict.cp.bind("cap.fs.read", lambda a: {})
        finally:
            strict.close()


if __name__ == "__main__":
    unittest.main()
