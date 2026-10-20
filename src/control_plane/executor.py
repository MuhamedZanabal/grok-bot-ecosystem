"""ControlPlane.execute: the single gate through which every action runs.

Pipeline (each step resolves to exactly one deterministic Status):
  validate -> kill switch -> policy -> handler bound -> idempotency replay
  -> approval (risk >= 2) -> rate limit -> loop budget -> claim op -> run handler
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from enum import Enum
import json
import re
import time
from typing import Any, Callable, Mapping

from .approvals import ApprovalAuthority, ApprovalError
from .audit import AuditLog
from .gateway import ActorGateway
from .hardening import set_non_dumpable
from .handlers import HandlerFailure, HandlerTimeout, IsolatedHandler, TrustedHostHandler
from .canonical import CanonicalizationError, args_sha256, canonical_json
from .governor import LoopGovernor, LoopState
from .killswitch import KillSwitch
from .ledger import IdempotencyLedger, OpState
from .policy import ACTOR_ID, CAPABILITY_ID, PolicyEngine
from .ratelimit import RateLimiter
from .schema import ArgsInvalid, validate as validate_args

OPERATION_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
MAX_ARGS_BYTES = 64 * 1024
_REQUIRED = {"operation_id", "actor", "capability", "args"}
_ALLOWED = _REQUIRED | {"approval_token", "dry_run", "budget_scope"}
SCOPE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class Status(str, Enum):
    EXECUTED = "executed"
    REPLAYED = "replayed"
    REJECTED = "rejected"                  # malformed request
    DENIED = "denied"                      # policy said no
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_INVALID = "approval_invalid"
    KILLED = "killed"
    CYCLE_ABORTED = "cycle_aborted"
    RATE_LIMITED = "rate_limited"
    CONFLICT = "conflict"                  # operation_id reused with different call
    IN_DOUBT = "in_doubt"                  # prior attempt crashed mid-operation
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    DRY_RUN = "dry_run"                    # every non-consuming check passed; nothing executed


@dataclass(frozen=True)
class Outcome:
    status: Status
    operation_id: str | None
    reason: str = ""
    result: Any = None


Handler = Callable[[dict], Any]


class HostHardeningError(RuntimeError):
    """The host process could not be made non-dumpable; the control plane refuses to start."""


class ControlPlane:
    def __init__(self, *, policy: PolicyEngine, approvals: ApprovalAuthority,
                 ledger: IdempotencyLedger, audit: AuditLog, kill_switch: KillSwitch,
                 governor: LoopGovernor, rate_limiter: RateLimiter | None = None,
                 handler_timeout_s: float = 30.0, allow_inprocess_handlers: bool = False,
                 require_host_hardening: bool = True, economics=None) -> None:
        hardened = set_non_dumpable()
        if require_host_hardening and not hardened:
            raise HostHardeningError("prctl(PR_SET_DUMPABLE, 0) failed; refusing to start")
        audit.append("host_hardening", non_dumpable=hardened, required=require_host_hardening)
        self.policy, self.approvals, self.ledger = policy, approvals, ledger
        self.audit, self.kill, self.governor = audit, kill_switch, governor
        self.rate_limiter = rate_limiter
        self.handler_timeout_s = handler_timeout_s
        self._allow_inprocess = allow_inprocess_handlers
        self._handlers: dict[str, Handler] = {}
        self.economics = economics      # EconomicsLedger: spend guard + meter (cycle-009, D-0024)

    def bind(self, capability_id: str, handler: Handler) -> None:
        cap = self.policy.capability(capability_id)
        if cap is None:
            raise ValueError(f"cannot bind handler to unregistered capability {capability_id}")
        if cap.schema_json is None:
            raise ValueError(f"capability {capability_id} has no args_schema; refusing to bind")
        isolated = isinstance(handler, IsolatedHandler)
        trusted_host = isinstance(handler, TrustedHostHandler)
        if not (isolated or trusted_host) and not self._allow_inprocess:
            raise ValueError("in-process handlers are disabled; bind an IsolatedHandler")
        self._handlers[capability_id] = handler
        self.audit.append("handler_bound", capability=capability_id, isolated=isolated,
                          handler_type=type(handler).__name__,
                          isolation=getattr(handler, "isolation", "inprocess"))

    def gateway(self, actor: str) -> ActorGateway:
        """Hand an agent runtime this, never the ControlPlane or a handler."""
        return ActorGateway(actor, self.execute, self.policy.granted)

    # ------------------------------------------------------------------ helpers
    def _out(self, status: Status, op: str | None, reason: str = "", result: Any = None,
             **extra: Any) -> Outcome:
        self.audit.append("decision", status=status.value, operation_id=op, reason=reason, **extra)
        return Outcome(status, op, reason, result)

    @staticmethod
    def _validate(request: Any) -> str | None:
        if not isinstance(request, Mapping):
            return "request_not_mapping"
        keys = set(request.keys())
        if not _REQUIRED <= keys:
            return "missing_fields:" + ",".join(sorted(_REQUIRED - keys))
        if not keys <= _ALLOWED:
            return "unknown_fields:" + ",".join(sorted(map(str, keys - _ALLOWED)))
        op, actor, cap, args = (request[k] for k in ("operation_id", "actor", "capability", "args"))
        if not isinstance(op, str) or not OPERATION_ID.fullmatch(op):
            return "bad_operation_id"
        if not isinstance(actor, str) or not ACTOR_ID.fullmatch(actor):
            return "bad_actor"
        if not isinstance(cap, str) or not CAPABILITY_ID.fullmatch(cap):
            return "bad_capability"
        if not isinstance(args, dict):
            return "args_not_object"
        try:
            if len(canonical_json(args).encode("utf-8")) > MAX_ARGS_BYTES:
                return "args_too_large"
        except CanonicalizationError:
            return "args_not_canonical_json"
        tok = request.get("approval_token")
        if tok is not None and not isinstance(tok, str):
            return "bad_approval_token_type"
        if not isinstance(request.get("dry_run", False), bool):
            return "bad_dry_run"
        scope = request.get("budget_scope", {})
        if not isinstance(scope, dict) or not set(scope) <= {"run", "job"} or not all(
                isinstance(v, str) and SCOPE_ID.fullmatch(v) for v in scope.values()):
            return "bad_budget_scope"
        return None

    # ------------------------------------------------------------------ execute
    def execute(self, request: Any, cost: float = 0.0) -> Outcome:
        err = self._validate(request)
        if err:
            op = request.get("operation_id") if isinstance(request, Mapping) else None
            return self._out(Status.REJECTED, op if isinstance(op, str) else None, err)
        op, actor, cap, args = (request[k] for k in ("operation_id", "actor", "capability", "args"))
        ctx = {"actor": actor, "capability": cap}

        if self.kill.engaged():
            return self._out(Status.KILLED, op, "kill_switch_engaged", **ctx)

        decision = self.policy.evaluate(actor, cap)
        if decision.policy_version is not None:
            ctx["policy_version"] = decision.policy_version
        if not decision.allowed:
            return self._out(Status.DENIED, op, decision.reason, **ctx)
        handler = self._handlers.get(cap)
        if handler is None:
            return self._out(Status.DENIED, op, "deny:no_handler_bound", **ctx)

        try:
            validate_args(self.policy.capability(cap).schema, args)
        except ArgsInvalid as exc:
            return self._out(Status.REJECTED, op, f"schema:{exc.code}", **ctx)

        digest = args_sha256(args)
        prior = self.ledger.get(op)
        if prior is not None:
            return self._from_prior(prior, op, cap, actor, digest, ctx)

        if decision.requires_approval:
            token = request.get("approval_token")
            if token is None:
                return self._out(Status.APPROVAL_REQUIRED, op, f"risk_level_{int(decision.risk)}", **ctx)
            try:
                claims = self.approvals.verify(token, actor=actor, capability=cap,
                                               operation_id=op, args=args)
            except ApprovalError as exc:
                return self._out(Status.APPROVAL_INVALID, op, exc.code, **ctx)
            ctx["approval_id"] = claims["token_id"]
            ctx["approver"] = claims["approver"]

        scope, econ = dict(request.get("budget_scope", {})), self.economics
        if request.get("dry_run", False):   # rate limiter and governor consume budget: not evaluated
            est = None
            if econ is not None:
                ok, why, est = econ.admit(actor, cap, op, scope, reserve=False)
                if not ok:
                    return self._out(Status.DENIED, op, f"budget:{why}", **ctx)
            return self._out(Status.DRY_RUN, op, "would_execute", {"estimated_cost": est}, **ctx)

        if self.rate_limiter is not None and not self.rate_limiter.allow(cap):
            return self._out(Status.RATE_LIMITED, op, "rate_limited", **ctx)

        try:
            state = self.governor.admit(cost)
        except ValueError:
            return self._out(Status.REJECTED, op, "bad_cost", **ctx)
        if state is LoopState.KILLED:
            return self._out(Status.KILLED, op, self.governor.reason, **ctx)
        if state is not LoopState.RUNNING:
            return self._out(Status.CYCLE_ABORTED, op, self.governor.reason, **ctx)

        if econ is not None:  # cumulative caps deny BEFORE execution; reservation is persisted
            ok, why, _ = econ.admit(actor, cap, op, scope)
            if not ok:
                return self._out(Status.DENIED, op, f"budget:{why}", **ctx)
        if not self.ledger.begin(op, cap, actor, digest):  # lost a race with a duplicate
            return self._from_prior(self.ledger.get(op), op, cap, actor, digest, ctx)
        self.audit.append("execute_begin", operation_id=op, args_sha256=digest, cost=cost, **ctx)
        if econ is None:
            return self._run(handler, op, args, ctx)
        mark, t0 = econ.mark(cap), time.perf_counter()
        out = self._run(handler, op, args, ctx)
        econ.record_operation(op, actor, cap, scope, out.status.value, out.reason, time.perf_counter() - t0,
                              mark, int(decision.risk))
        return out

    def _from_prior(self, prior, op, cap, actor, digest, ctx) -> Outcome:
        if (prior.capability, prior.actor, prior.args_sha256) != (cap, actor, digest):
            return self._out(Status.CONFLICT, op, "operation_id_reused_with_different_call", **ctx)
        if prior.state is OpState.SUCCEEDED:
            return self._out(Status.REPLAYED, op, "idempotent_replay", prior.result, **ctx)
        if prior.state is OpState.PENDING:
            return self._out(Status.IN_DOUBT, op, "prior_attempt_incomplete", **ctx)
        terminal = Status.FAILED if prior.state is OpState.FAILED else Status.TIMED_OUT
        return self._out(terminal, op, f"replay_of_{prior.state.value}:{prior.error}", **ctx)

    def _run(self, handler: Handler, op: str, args: dict, ctx: dict) -> Outcome:
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(handler, json.loads(canonical_json(args)))  # handler gets a copy
        try:
            result = future.result(timeout=self.handler_timeout_s)
        except FutureTimeout:
            pool.shutdown(wait=False, cancel_futures=True)
            self.ledger.finish(op, OpState.TIMED_OUT, error="handler_timeout")
            return self._out(Status.TIMED_OUT, op, "handler_timeout", **ctx)
        except HandlerTimeout:
            pool.shutdown(wait=False)
            self.ledger.finish(op, OpState.TIMED_OUT, error="handler_timeout:worker_killed")
            return self._out(Status.TIMED_OUT, op, "handler_timeout:worker_killed", **ctx)
        except HandlerFailure as exc:
            pool.shutdown(wait=False)
            reason = f"worker:{exc.code}"
            self.ledger.finish(op, OpState.FAILED, error=reason)
            return self._out(Status.FAILED, op, reason, **ctx)
        except Exception as exc:  # handler failure is data, not a crash
            pool.shutdown(wait=False)
            reason = f"handler_error:{type(exc).__name__}"
            self.ledger.finish(op, OpState.FAILED, error=reason)
            return self._out(Status.FAILED, op, reason, **ctx)
        pool.shutdown(wait=False)
        try:
            canonical_json(result)
        except CanonicalizationError:
            self.ledger.finish(op, OpState.FAILED, error="handler_result_not_json")
            return self._out(Status.FAILED, op, "handler_result_not_json", **ctx)
        self.ledger.finish(op, OpState.SUCCEEDED, result=result)
        return self._out(Status.EXECUTED, op, "executed", result, **ctx)
