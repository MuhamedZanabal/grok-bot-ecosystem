"""Capability policy engine. Default DENY; only explicit (actor, capability) grants allow.

Risk levels:
  0 READ          read-only, no side effects
  1 LOCAL_WRITE   reversible, box-local side effects
  2 EXTERNAL      externally visible or consequential (message, post, ticket)
  3 IRREVERSIBLE  financial or irreversible (payment, deletion, trade)
Level 2 and 3 additionally require a valid approval token bound to the exact call.
Wildcard grants are intentionally unsupported.
"""

from dataclasses import dataclass
from enum import IntEnum
import re

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
    id: str
    risk: RiskLevel
    description: str = ""

    def __post_init__(self) -> None:
        if not CAPABILITY_ID.fullmatch(self.id):
            raise ValueError(f"invalid capability id: {self.id!r}")
        if not isinstance(self.risk, RiskLevel):
            raise ValueError("risk must be a RiskLevel")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    risk: RiskLevel | None = None

    @property
    def requires_approval(self) -> bool:
        return self.allowed and self.risk is not None and self.risk >= APPROVAL_THRESHOLD


class PolicyEngine:
    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}
        self._grants: set[tuple[str, str]] = set()

    def register(self, cap: Capability) -> None:
        if cap.id in self._caps and self._caps[cap.id] != cap:
            raise ValueError(f"capability {cap.id} already registered with different risk")
        self._caps[cap.id] = cap

    def grant(self, actor: str, capability_id: str) -> None:
        if not ACTOR_ID.fullmatch(actor):
            raise ValueError(f"invalid actor id: {actor!r}")
        if capability_id not in self._caps:
            raise ValueError(f"cannot grant unregistered capability {capability_id}")
        self._grants.add((actor, capability_id))

    def revoke(self, actor: str, capability_id: str) -> None:
        self._grants.discard((actor, capability_id))

    def capability(self, capability_id: str) -> Capability | None:
        return self._caps.get(capability_id)

    def evaluate(self, actor: str, capability_id: str) -> Decision:
        cap = self._caps.get(capability_id)
        if cap is None:
            return Decision(False, "deny:unknown_capability")
        if (actor, capability_id) not in self._grants:
            return Decision(False, "deny:no_grant", cap.risk)
        return Decision(True, "allow:explicit_grant", cap.risk)
