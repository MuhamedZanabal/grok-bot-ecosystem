"""Capability policy engine. Default DENY; only explicit (actor, capability) grants allow.

Risk levels:
  0 READ          read-only, no side effects
  1 LOCAL_WRITE   reversible, box-local side effects
  2 EXTERNAL      externally visible or consequential (message, post, ticket)
  3 IRREVERSIBLE  financial or irreversible (payment, deletion, trade)
Level 2 and 3 additionally require a valid approval token bound to the exact call.
Wildcard grants are intentionally unsupported. Grants persist in a GrantStore
(in-memory SQLite by default) and every grant/revoke is versioned and audited.
"""

from dataclasses import dataclass
from enum import IntEnum
import json
import re
from typing import TYPE_CHECKING

from .grant_store import GrantStore
from .schema import freeze

if TYPE_CHECKING:
    from .audit import AuditLog

CAPABILITY_ID = re.compile(r"^cap\.[a-z0-9_]+\.[a-z0-9_]+$")
ACTOR_ID = re.compile(r"^[a-z0-9._-]{1,64}$")


class RiskLevel(IntEnum):
    READ = 0
    LOCAL_WRITE = 1
    EXTERNAL = 2
    IRREVERSIBLE = 3


APPROVAL_THRESHOLD = RiskLevel.EXTERNAL


@dataclass(frozen=True)
class Capability:
    """A registered capability. `args_schema` is a JSON-Schema-subset dict (control_plane.schema);
    it is validated and stored as canonical JSON (`schema_json`) so it cannot be mutated later."""
    id: str
    risk: RiskLevel
    description: str = ""
    args_schema: dict | None = None
    schema_json: str | None = None

    def __post_init__(self) -> None:
        if not CAPABILITY_ID.fullmatch(self.id):
            raise ValueError(f"invalid capability id: {self.id!r}")
        if not isinstance(self.risk, RiskLevel):
            raise ValueError("risk must be a RiskLevel")
        if self.args_schema is not None and self.schema_json is not None:
            raise ValueError("pass args_schema or schema_json, not both")
        src = self.args_schema if self.args_schema is not None else (
            json.loads(self.schema_json) if self.schema_json is not None else None)
        if src is not None:
            if not isinstance(src, dict) or src.get("type") != "object":
                raise ValueError("args_schema must be an object schema")
            object.__setattr__(self, "schema_json", freeze(src))  # re-validated either way
            object.__setattr__(self, "args_schema", None)  # keep only the frozen form

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Capability) and (self.id, self.risk, self.description, self.schema_json)
                == (other.id, other.risk, other.description, other.schema_json))

    def __hash__(self) -> int:
        return hash((self.id, self.risk, self.schema_json))

    @property
    def schema(self) -> dict | None:
        return json.loads(self.schema_json) if self.schema_json is not None else None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    risk: RiskLevel | None = None
    policy_version: int | None = None

    @property
    def requires_approval(self) -> bool:
        return self.allowed and self.risk is not None and self.risk >= APPROVAL_THRESHOLD


class PolicyEngine:
    def __init__(self, store: GrantStore | None = None, audit: "AuditLog | None" = None) -> None:
        self._caps: dict[str, Capability] = {}
        self._store = store if store is not None else GrantStore()
        self._audit = audit

    def register(self, cap: Capability) -> None:
        if cap.id in self._caps and self._caps[cap.id] != cap:
            raise ValueError(f"capability {cap.id} already registered with different definition")
        self._caps[cap.id] = cap

    def _change(self, action: str, actor: str, capability_id: str, by: str, reason: str) -> int:
        if not ACTOR_ID.fullmatch(actor):
            raise ValueError(f"invalid actor id: {actor!r}")
        cap = self._caps.get(capability_id)
        if cap is None:
            raise ValueError(f"cannot {action} unregistered capability {capability_id}")
        if not isinstance(by, str) or not by or not isinstance(reason, str):
            raise ValueError("by must be a non-empty string and reason a string")
        version = self._store.apply(action, actor, capability_id, int(cap.risk), by, reason)
        if self._audit is not None:
            self._audit.append(f"policy_{action}", actor=actor, capability=capability_id,
                               risk=int(cap.risk), policy_version=version, by=by, reason=reason)
        return version

    def grant(self, actor: str, capability_id: str, *, by: str = "operator", reason: str = "") -> int:
        return self._change("grant", actor, capability_id, by, reason)

    def revoke(self, actor: str, capability_id: str, *, by: str = "operator", reason: str = "") -> int:
        return self._change("revoke", actor, capability_id, by, reason)

    @property
    def version(self) -> int:
        return self._store.version()

    def capability(self, capability_id: str) -> Capability | None:
        return self._caps.get(capability_id)

    def granted(self, actor: str) -> list[Capability]:
        """Capabilities `actor` may currently use (registered, granted, risk unchanged)."""
        return [self._caps[c] for c, r in self._store.granted(actor)
                if c in self._caps and int(self._caps[c].risk) == r]

    def evaluate(self, actor: str, capability_id: str) -> Decision:
        cap = self._caps.get(capability_id)
        if cap is None:
            return Decision(False, "deny:unknown_capability")
        row = self._store.lookup(actor, capability_id)
        if row is None:
            return Decision(False, "deny:no_grant", cap.risk)
        granted_risk, version = row
        if granted_risk != int(cap.risk):
            return Decision(False, "deny:grant_risk_mismatch", cap.risk, version)
        return Decision(True, "allow:explicit_grant", cap.risk, version)
