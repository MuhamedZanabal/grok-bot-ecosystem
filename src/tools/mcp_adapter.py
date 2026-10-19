"""MCP tool-descriptor adapter (no transport, no network).

Input: the JSON result of an MCP `tools/list` call, as produced by any server:
    {"tools": [{"name": str, "description"?: str, "inputSchema": {...}, "annotations"?: {...}}]}
Output: ToolDescriptor records. Everything in the payload is UNTRUSTED:
  * names are mapped to `cap.mcp_<server>.<sanitized_name>`; collisions are rejected;
  * inputSchema must fit control_plane.schema's subset, else the tool is rejected;
  * descriptions are truncated and kept as data (they can contain prompt injection);
  * annotations (readOnlyHint, destructiveHint, ...) are recorded but NEVER lower risk:
    every imported tool is proposed at RiskLevel.IRREVERSIBLE until an operator says otherwise.
"""

from dataclasses import dataclass, field
import json
import re

from control_plane import RiskLevel
from control_plane.schema import SchemaError, freeze

SERVER = re.compile(r"[a-z0-9_]{1,32}")
MAX_TOOLS = 256
MAX_DESCRIPTION = 1024
MAX_PAYLOAD_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ToolDescriptor:
    source: str
    external_name: str
    capability_id: str | None
    description: str
    schema_json: str | None
    proposed_risk: RiskLevel
    annotations: str = "{}"
    error: str | None = None          # set => rejected at import
    extra: dict = field(default_factory=dict, compare=False)


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower())[:48].strip("_") or "_"


def parse_tools_list(server: str, payload: object) -> list[ToolDescriptor]:
    if not isinstance(server, str) or not SERVER.fullmatch(server):
        raise ValueError("server name must match [a-z0-9_]{1,32}")
    if isinstance(payload, (str, bytes)):
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload too large")
        payload = json.loads(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
        raise ValueError("payload must be an object with a 'tools' list")
    tools = payload["tools"]
    if len(tools) > MAX_TOOLS:
        raise ValueError("too many tools")
    source = f"mcp:{server}"
    out: list[ToolDescriptor] = []
    seen: dict[str, str] = {}
    for t in tools:
        name = t.get("name") if isinstance(t, dict) else None
        if not isinstance(name, str) or not name or len(name) > 128:
            out.append(ToolDescriptor(source, str(name)[:128], None, "", None, RiskLevel.IRREVERSIBLE,
                                      error="bad_name"))
            continue
        cap_id = f"cap.mcp_{server}.{_sanitize(name)}"
        desc = t.get("description", "")
        desc = desc[:MAX_DESCRIPTION] if isinstance(desc, str) else ""
        ann = t.get("annotations", {})
        ann_json = json.dumps(ann if isinstance(ann, dict) else {}, sort_keys=True)[:2048]
        error, schema_json = None, None
        if cap_id in seen:
            error = f"name_collision_with:{seen[cap_id]}"[:160]
        else:
            seen[cap_id] = name
            schema = t.get("inputSchema")
            try:
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    raise SchemaError("input_schema_not_object")
                schema_json = freeze(schema)
            except SchemaError as exc:
                error = f"unsupported_schema:{exc}"[:160]
        out.append(ToolDescriptor(source, name, None if error else cap_id, desc, schema_json,
                                  RiskLevel.IRREVERSIBLE, ann_json, error))
    return out
