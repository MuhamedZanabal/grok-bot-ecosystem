"""Typed tool registry: the single place tools become (or fail to become) capabilities.

Trust states:
  candidate  known but untrusted (e.g. imported from MCP). NOT registered with policy,
             no handler bound, execution_allowed=False; it cannot be granted or executed.
  accepted   an operator called accept(): capability registered at an operator-chosen risk
             and bound to an out-of-process SubprocessHandler. Execution still requires a grant
             (and an approval for risk >= 2).
  rejected   import failed validation, or an operator rejected it. Terminal.
Every state change is versioned in a RegistryStore (SQLite) and audited. On construction
the registry reloads persisted state: accepted tools are re-registered and re-bound to a
sandboxed SubprocessHandler from their stored WorkerSpec; candidates stay inert.
"""

from dataclasses import asdict, dataclass, replace
from enum import Enum
import json

from control_plane import AuditLog, Capability, ControlPlane, PolicyEngine, RiskLevel

from .mcp_adapter import ToolDescriptor, parse_tools_list
from .registry_store import RegistryStore
from .subprocess_handler import SubprocessHandler, WorkerSpec


class TrustState(str, Enum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ToolSpec:
    capability_id: str
    source: str
    description: str
    schema_json: str
    risk: RiskLevel
    trust: TrustState
    reason: str = ""

    @property
    def execution_allowed(self) -> bool:
        return self.trust is TrustState.ACCEPTED


class ToolRegistry:
    def __init__(self, policy: PolicyEngine, control_plane: ControlPlane, audit: AuditLog,
                 store: RegistryStore | None = None) -> None:
        self._policy, self._cp, self._audit = policy, control_plane, audit
        self._store = store if store is not None else RegistryStore()
        self._tools: dict[str, ToolSpec] = {}
        self.rejected_imports: list[ToolDescriptor] = []
        self._load()

    def _load(self) -> None:
        for row in self._store.current():
            spec = ToolSpec(row["capability_id"], row["source"], row["description"], row["schema_json"],
                            RiskLevel(row["risk"]), TrustState(row["trust"]), row["reason"])
            if spec.trust is TrustState.ACCEPTED:
                w = json.loads(row["worker_json"])
                w["env_allow"] = tuple(w.get("env_allow", ()))
                w["egress"] = tuple(w.get("egress", ()))
                self._activate(spec, WorkerSpec(**w))  # re-validates spec + schema
            self._tools[spec.capability_id] = spec
            self._audit.append("tool_reloaded", capability=spec.capability_id, trust=spec.trust.value,
                               registry_version=row["version"])

    @property
    def version(self) -> int:
        return self._store.version()

    def get(self, capability_id: str) -> ToolSpec | None:
        return self._tools.get(capability_id)

    def all(self) -> list[ToolSpec]:
        return [self._tools[k] for k in sorted(self._tools)]

    def _set(self, spec: ToolSpec, event: str, by: str, reason: str,
             worker: WorkerSpec | None = None) -> ToolSpec:
        version = self._store.record(
            event, capability_id=spec.capability_id, source=spec.source, trust=spec.trust.value,
            risk=int(spec.risk), description=spec.description, schema_json=spec.schema_json,
            worker_json=json.dumps(asdict(worker), sort_keys=True) if worker is not None else None,
            reason=reason, by=by)
        self._tools[spec.capability_id] = spec
        self._audit.append(event, capability=spec.capability_id, source=spec.source, trust=spec.trust.value,
                           risk=int(spec.risk), by=by, reason=reason, registry_version=version)
        return spec

    def _activate(self, spec: ToolSpec, worker: WorkerSpec) -> None:
        if not spec.execution_allowed:
            raise PermissionError(f"{spec.capability_id} is not accepted")
        if worker.egress and spec.risk < RiskLevel.EXTERNAL:
            # network egress can send data off the box: always approval-gated (D-0015)
            raise ValueError("egress_requires_risk_external")
        handler = SubprocessHandler(worker, audit=self._audit, capability=spec.capability_id)
        cap = Capability(spec.capability_id, spec.risk, spec.description, schema_json=spec.schema_json)
        self._policy.register(cap)
        self._cp.bind(spec.capability_id, handler)

    def register_local(self, capability_id: str, risk: RiskLevel, description: str, args_schema: dict,
                       worker: WorkerSpec, *, by: str, reason: str = "") -> ToolSpec:
        """Repository-owned tool: accepted on registration, still out-of-process, still needs a grant."""
        if capability_id in self._tools:
            raise ValueError(f"{capability_id} already registered")
        schema_json = Capability(capability_id, risk, description, args_schema).schema_json
        spec = ToolSpec(capability_id, "local", description, schema_json, risk, TrustState.ACCEPTED)
        self._activate(spec, worker)
        return self._set(spec, "tool_registered", by, reason, worker)

    def import_mcp(self, server: str, tools_list_payload: object, *, by: str) -> list[ToolSpec]:
        """Import external MCP tool definitions as CANDIDATES only."""
        imported = []
        for d in parse_tools_list(server, tools_list_payload):
            if d.error or d.capability_id is None:
                self.rejected_imports.append(d)
                self._audit.append("tool_import_rejected", source=d.source, name=d.external_name[:128],
                                   error=d.error, by=by)
                continue
            if d.capability_id in self._tools or self._policy.capability(d.capability_id) is not None:
                self.rejected_imports.append(replace(d, error="already_registered"))
                self._audit.append("tool_import_rejected", source=d.source, name=d.external_name[:128],
                                   error="already_registered", by=by)
                continue
            spec = ToolSpec(d.capability_id, d.source, d.description, d.schema_json, d.proposed_risk,
                            TrustState.CANDIDATE, f"annotations={d.annotations}"[:512])
            imported.append(self._set(spec, "tool_candidate", by, "mcp_import"))
        return imported

    def accept(self, capability_id: str, *, risk: RiskLevel, worker: WorkerSpec, by: str, reason: str) -> ToolSpec:
        spec = self._tools.get(capability_id)
        if spec is None or spec.trust is not TrustState.CANDIDATE:
            raise ValueError(f"{capability_id} is not a candidate")
        if not isinstance(risk, RiskLevel):
            raise ValueError("risk must be a RiskLevel chosen by the operator")
        if not by or not reason:
            raise ValueError("accept requires an operator identity and a reason")
        accepted = replace(spec, risk=risk, trust=TrustState.ACCEPTED, reason=reason)
        self._activate(accepted, worker)
        return self._set(accepted, "tool_accepted", by, reason, worker)

    def reject(self, capability_id: str, *, by: str, reason: str) -> ToolSpec:
        spec = self._tools.get(capability_id)
        if spec is None or spec.trust is not TrustState.CANDIDATE:
            raise ValueError(f"{capability_id} is not a candidate")
        return self._set(replace(spec, trust=TrustState.REJECTED, reason=reason), "tool_rejected", by, reason)
