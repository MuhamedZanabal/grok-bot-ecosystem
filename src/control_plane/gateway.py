"""Actor-bound gateway: the only object an agent runtime receives.

It exposes exactly two operations, both pure data in / data out:
  submit(...)  -> Outcome   (always via ControlPlane.execute, actor fixed at construction)
  catalog()    -> list[dict] of capabilities granted to this actor (id, risk, description)
There is no way to obtain a handler or the ControlPlane from its public surface. This is
a data-flow boundary, not a memory boundary: code in the same Python process could
still introspect it (see security_log.md S-0003).
"""

from typing import Any, Callable

from .policy import ACTOR_ID


class ActorGateway:
    __slots__ = ("_execute", "_catalog", "_actor")

    def __init__(self, actor: str, execute: Callable[..., Any], catalog: Callable[[str], list]) -> None:
        if not isinstance(actor, str) or not ACTOR_ID.fullmatch(actor):
            raise ValueError(f"invalid actor id: {actor!r}")
        object.__setattr__(self, "_actor", actor)
        object.__setattr__(self, "_execute", execute)
        object.__setattr__(self, "_catalog", catalog)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ActorGateway is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("ActorGateway is immutable")

    @property
    def actor(self) -> str:
        return self._actor

    def __repr__(self) -> str:
        return f"ActorGateway(actor={self._actor!r})"

    def __reduce_ex__(self, protocol):  # refuse pickling/copying out the bound callables
        raise TypeError("ActorGateway cannot be serialized or copied")

    def submit(self, *, operation_id: str, capability: str, args: dict,
               approval_token: str | None = None, cost: float = 0.0):
        request = {"operation_id": operation_id, "actor": self._actor,
                   "capability": capability, "args": args}
        if approval_token is not None:
            request["approval_token"] = approval_token
        return self._execute(request, cost)

    def catalog(self) -> list[dict]:
        return [{"capability": c.id, "risk": int(c.risk), "description": c.description}
                for c in self._catalog(self._actor)]
