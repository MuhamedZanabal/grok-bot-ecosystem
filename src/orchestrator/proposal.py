"""Strict parser: untrusted model text -> typed proposal.

Accepted forms (exactly one JSON object, optionally inside a single ```json fence):
  {"type": "action", "capability": "cap.x.y", "args": {...},
   "idempotency_key"?: "k", "rationale"?: "..."}
  {"type": "final", "answer": "..."}
Anything else raises ProposalError with a stable code. Duplicate keys, NaN/Infinity,
unknown fields, oversize and over-deep payloads are all rejected.
"""

from dataclasses import dataclass
import json
import re
from typing import Any

from control_plane.policy import CAPABILITY_ID

MAX_OUTPUT_BYTES = 16 * 1024
MAX_DEPTH = 16
MAX_RATIONALE = 2000
MAX_ANSWER = 8000
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._-]{1,64}")
_FENCE = re.compile(r"```(?:json)?\s*\n(.*)\n\s*```", re.DOTALL)
_ACTION_REQUIRED = {"type", "capability", "args"}
_ACTION_ALLOWED = _ACTION_REQUIRED | {"idempotency_key", "rationale"}
_FINAL_KEYS = {"type", "answer"}


class ProposalError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ActionProposal:
    capability: str
    args: dict
    idempotency_key: str | None = None
    rationale: str = ""


@dataclass(frozen=True)
class FinalProposal:
    answer: str


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise ProposalError("duplicate_key")
        out[k] = v
    return out


def _reject_constant(name: str) -> Any:
    raise ProposalError("non_finite_number")


def _depth(v: Any, d: int = 0) -> int:
    if d > MAX_DEPTH:
        return d
    if isinstance(v, dict):
        return max([_depth(x, d + 1) for x in v.values()], default=d + 1)
    if isinstance(v, list):
        return max([_depth(x, d + 1) for x in v], default=d + 1)
    return d


def parse_proposal(text: object) -> ActionProposal | FinalProposal:
    if not isinstance(text, str):
        raise ProposalError("output_not_text")
    if len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ProposalError("output_too_large")
    body = text.strip()
    fenced = _FENCE.fullmatch(body)
    if fenced:
        body = fenced.group(1).strip()
    if not body.startswith("{"):
        raise ProposalError("not_a_json_object")
    try:
        obj = json.loads(body, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except ProposalError:
        raise
    except (ValueError, RecursionError):
        raise ProposalError("invalid_json") from None
    if not isinstance(obj, dict):
        raise ProposalError("not_a_json_object")
    if _depth(obj) > MAX_DEPTH:
        raise ProposalError("too_deep")
    kind = obj.get("type")
    keys = set(obj)
    if kind == "final":
        if keys != _FINAL_KEYS:
            raise ProposalError("bad_fields")
        if not isinstance(obj["answer"], str) or len(obj["answer"]) > MAX_ANSWER:
            raise ProposalError("bad_answer")
        return FinalProposal(obj["answer"])
    if kind == "action":
        if not _ACTION_REQUIRED <= keys or not keys <= _ACTION_ALLOWED:
            raise ProposalError("bad_fields")
        cap, args = obj["capability"], obj["args"]
        if not isinstance(cap, str) or not CAPABILITY_ID.fullmatch(cap):
            raise ProposalError("bad_capability")
        if not isinstance(args, dict):
            raise ProposalError("args_not_object")
        key = obj.get("idempotency_key")
        if key is not None and (not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key)):
            raise ProposalError("bad_idempotency_key")
        rationale = obj.get("rationale", "")
        if not isinstance(rationale, str) or len(rationale) > MAX_RATIONALE:
            raise ProposalError("bad_rationale")
        return ActionProposal(cap, args, key, rationale)
    raise ProposalError("unknown_type")
