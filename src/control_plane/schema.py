"""Argument schemas: a small, strict JSON-Schema subset (stdlib only).

Supported keywords
  type (object|string|integer|number|boolean|array|null), properties, required,
  additionalProperties (bool), enum, minLength, maxLength, pattern, minimum, maximum,
  items, minItems, maxItems; annotations ignored: title, description, default, examples, $schema.
Deliberate deviations (stricter than JSON Schema):
  * a missing additionalProperties means FALSE (unknown args are rejected);
  * pattern must match the WHOLE string (re.fullmatch), not search;
  * booleans are never integers/numbers; numbers must be finite;
  * any other keyword ($ref, oneOf, format, ...) makes the schema invalid (fail closed).
"""

import json
import math
import re
from typing import Any

ANNOTATIONS = {"title", "description", "default", "examples", "$schema"}
KEYWORDS = {"type", "properties", "required", "additionalProperties", "enum", "minLength",
            "maxLength", "pattern", "minimum", "maximum", "items", "minItems", "maxItems"} | ANNOTATIONS
TYPES = {"object", "string", "integer", "number", "boolean", "array", "null"}
MAX_SCHEMA_DEPTH = 8


class SchemaError(ValueError):
    """The schema itself is invalid or uses an unsupported keyword."""


class ArgsInvalid(ValueError):
    """Arguments do not satisfy the schema. `code` is stable: '<json-path>:<rule>'."""

    def __init__(self, path: str, rule: str) -> None:
        self.code = f"{path}:{rule}"
        super().__init__(self.code)


def _nonneg_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_schema(schema: Any, depth: int = 0) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise SchemaError("schema_too_deep")
    if not isinstance(schema, dict):
        raise SchemaError("schema_not_object")
    unknown = set(schema) - KEYWORDS
    if unknown:
        raise SchemaError("unsupported_keyword:" + ",".join(sorted(unknown)))
    t = schema.get("type")
    if t not in TYPES:
        raise SchemaError("missing_or_bad_type")
    if "properties" in schema:
        if t != "object" or not isinstance(schema["properties"], dict):
            raise SchemaError("bad_properties")
        for sub in schema["properties"].values():
            check_schema(sub, depth + 1)
    if "required" in schema:
        req = schema["required"]
        if (t != "object" or not isinstance(req, list) or not all(isinstance(r, str) for r in req)
                or not set(req) <= set(schema.get("properties", {}))):
            raise SchemaError("bad_required")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise SchemaError("additionalProperties_must_be_bool")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise SchemaError("bad_enum")
    for k in ("minLength", "maxLength", "minItems", "maxItems"):
        if k in schema and not _nonneg_int(schema[k]):
            raise SchemaError(f"bad_{k}")
    for k in ("minimum", "maximum"):
        if k in schema and not _num(schema[k]):
            raise SchemaError(f"bad_{k}")
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise SchemaError("bad_pattern")
        try:
            re.compile(schema["pattern"])
        except re.error:
            raise SchemaError("bad_pattern") from None
    if "items" in schema:
        if t != "array":
            raise SchemaError("bad_items")
        check_schema(schema["items"], depth + 1)


def freeze(schema: Any) -> str:
    """Validate and return the canonical JSON form (immutable storage)."""
    check_schema(schema)
    return json.dumps(schema, sort_keys=True, separators=(",", ":"))


def _type_ok(t: str, v: Any) -> bool:
    return {
        "object": isinstance(v, dict),
        "string": isinstance(v, str),
        "integer": isinstance(v, int) and not isinstance(v, bool),
        "number": _num(v),
        "boolean": isinstance(v, bool),
        "array": isinstance(v, list),
        "null": v is None,
    }[t]


def validate(schema: dict, value: Any, path: str = "$") -> None:
    t = schema["type"]
    if not _type_ok(t, value):
        raise ArgsInvalid(path, f"type_{t}")
    if "enum" in schema and value not in schema["enum"]:
        raise ArgsInvalid(path, "enum")
    if t == "object":
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in value:
                raise ArgsInvalid(f"{path}.{r}", "required")
        if not schema.get("additionalProperties", False):
            extra = sorted(set(value) - set(props))
            if extra:
                raise ArgsInvalid(f"{path}.{extra[0]}", "additional_property")
        for k in sorted(value):
            if k in props:
                validate(props[k], value[k], f"{path}.{k}")
    elif t == "string":
        if len(value) < schema.get("minLength", 0):
            raise ArgsInvalid(path, "minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ArgsInvalid(path, "maxLength")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ArgsInvalid(path, "pattern")
    elif t in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            raise ArgsInvalid(path, "minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ArgsInvalid(path, "maximum")
    elif t == "array":
        if len(value) < schema.get("minItems", 0):
            raise ArgsInvalid(path, "minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ArgsInvalid(path, "maxItems")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(schema["items"], item, f"{path}[{i}]")
