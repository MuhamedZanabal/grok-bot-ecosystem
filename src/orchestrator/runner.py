"""Bounded agent loop. Intelligence proposes; the control plane authorizes and executes.

Every run ends in exactly one RunState:
  completed          model returned a final answer
  awaiting_approval  an action needs a risk>=2 approval token (see PendingApproval)
  cycle_aborted      run or control-plane budget exhausted
  killed             kill switch engaged
  provider_failed    every provider/attempt timed out, was unavailable or errored
  malformed_output   more than max_malformed consecutive unparsable outputs
  blocked_limit      more than max_blocked actions were refused or failed
  in_doubt           the control plane reports a prior attempt crashed mid-operation

Resuming after approval = calling run() again with the same run_id and an approvals map
{operation_id: token}. Operation ids are derived deterministically from
(run_id, capability, args, idempotency_key), so completed actions replay from the
idempotency ledger instead of executing again.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import re
import time
from typing import Any, Callable, Mapping, Sequence

from control_plane import ActorGateway, AuditLog, Budget, KillSwitch, LoopGovernor, LoopState, Status
from control_plane.canonical import args_sha256, canonical_json

from .proposal import ActionProposal, FinalProposal, ProposalError, parse_proposal
from .provider import ModelProvider, ProviderError

RUN_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
MAX_TASK_CHARS = 8000
PROTOCOL = ('Reply with exactly one JSON object and nothing else: '
            '{"type":"action","capability":"<id from catalog>","args":{...},'
            '"idempotency_key":"<optional>","rationale":"<optional>"} '
            'or {"type":"final","answer":"<text>"}.')


class RunState(str, Enum):
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    CYCLE_ABORTED = "cycle_aborted"
    KILLED = "killed"
    PROVIDER_FAILED = "provider_failed"
    MALFORMED_OUTPUT = "malformed_output"
    BLOCKED_LIMIT = "blocked_limit"
    IN_DOUBT = "in_doubt"


@dataclass(frozen=True)
class RunConfig:
    max_model_calls: int = 8
    max_wall_seconds: float = 120.0
    max_model_cost: float = 1.0
    model_call_cost: float = 0.01        # ESTIMATE per call, in budget units; not metered
    provider_timeout_s: float = 30.0
    attempts_per_provider: int = 2
    max_malformed: int = 2               # consecutive malformed outputs tolerated
    max_blocked: int = 3                 # refused/failed actions tolerated per run
    max_observation_bytes: int = 4096


@dataclass(frozen=True)
class PendingApproval:
    operation_id: str
    actor: str
    capability: str
    args: dict
    args_sha256: str
    reason: str


@dataclass
class RunResult:
    run_id: str
    state: RunState
    reason: str
    answer: str | None = None
    pending: PendingApproval | None = None
    steps: list[dict] = field(default_factory=list)


_CONTINUE = {Status.EXECUTED, Status.REPLAYED}
_BLOCKED = {Status.DENIED, Status.REJECTED, Status.APPROVAL_INVALID, Status.CONFLICT,
            Status.RATE_LIMITED, Status.FAILED, Status.TIMED_OUT}
_TERMINAL = {Status.KILLED: RunState.KILLED, Status.CYCLE_ABORTED: RunState.CYCLE_ABORTED,
             Status.IN_DOUBT: RunState.IN_DOUBT}


def operation_id_for(run_id: str, p: ActionProposal) -> str:
    digest = hashlib.sha256(canonical_json([p.capability, p.args, p.idempotency_key]).encode()).hexdigest()
    return f"run:{run_id}:{digest[:32]}"


class AgentRunner:
    def __init__(self, gateway: ActorGateway, providers: Sequence[ModelProvider], audit: AuditLog,
                 config: RunConfig = RunConfig(), kill_switch: KillSwitch | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not isinstance(gateway, ActorGateway):
            raise TypeError("AgentRunner requires an ActorGateway, not a ControlPlane or handler")
        if not providers:
            raise ValueError("at least one provider is required")
        self._gw, self._providers, self._audit = gateway, list(providers), audit
        self._cfg, self._kill, self._clock = config, kill_switch, clock

    # ------------------------------------------------------------------ provider call
    def _call(self, provider: ModelProvider, request_json: str) -> tuple[object, str | None]:
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(provider.complete, request_json)
        try:
            return future.result(timeout=self._cfg.provider_timeout_s), None
        except FutureTimeout:
            return None, "provider_timeout"
        except ProviderError as exc:
            return None, exc.code
        except Exception as exc:
            return None, f"provider_error:{type(exc).__name__}"
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _end(self, res: RunResult, state: RunState, reason: str, **extra: Any) -> RunResult:
        res.state, res.reason = state, reason
        for k, v in extra.items():
            setattr(res, k, v)
        self._audit.append("run_end", run_id=res.run_id, state=state.value, reason=reason,
                           steps=len(res.steps))
        return res

    def _observe(self, outcome) -> dict:
        obs = {"role": "observation", "status": outcome.status.value, "reason": outcome.reason}
        if outcome.result is not None:
            text = canonical_json(outcome.result)
            if len(text.encode("utf-8")) > self._cfg.max_observation_bytes:
                text, obs["truncated"] = text.encode("utf-8")[: self._cfg.max_observation_bytes].decode(
                    "utf-8", "ignore"), True
            obs["result"] = text
        return obs

    # ------------------------------------------------------------------ main loop
    def run(self, task: str, run_id: str, approvals: Mapping[str, str] | None = None) -> RunResult:
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run_id")
        if not isinstance(task, str) or not task or len(task) > MAX_TASK_CHARS:
            raise ValueError("task must be a non-empty string <= 8000 chars")
        approvals = dict(approvals or {})
        cfg = self._cfg
        gov = LoopGovernor(Budget(cfg.max_model_calls, cfg.max_wall_seconds, cfg.max_model_cost),
                           self._kill, clock=self._clock)
        res = RunResult(run_id, RunState.COMPLETED, "")
        transcript: list[dict] = []
        malformed = blocked = 0
        self._audit.append("run_start", run_id=run_id, actor=self._gw.actor,
                           task_sha256=hashlib.sha256(task.encode()).hexdigest(),
                           providers=[p.name for p in self._providers])

        while True:
            request_json = canonical_json({
                "v": 1, "run_id": run_id, "task": task, "protocol": PROTOCOL,
                "catalog": self._gw.catalog(), "transcript": transcript})
            output, err = None, "no_attempt"
            for provider in self._providers:
                for attempt in range(1, cfg.attempts_per_provider + 1):
                    state = gov.admit(cfg.model_call_cost)
                    if state is LoopState.KILLED:
                        return self._end(res, RunState.KILLED, gov.reason)
                    if state is not LoopState.RUNNING:
                        return self._end(res, RunState.CYCLE_ABORTED, gov.reason)
                    output, err = self._call(provider, request_json)
                    self._audit.append(
                        "model_call", run_id=run_id, provider=provider.name, attempt=attempt,
                        error=err, output_len=len(output) if isinstance(output, str) else None,
                        output_sha256=hashlib.sha256(output.encode()).hexdigest()
                        if isinstance(output, str) else None)
                    if err is None:
                        break
                if err is None:
                    break
            if err is not None:
                return self._end(res, RunState.PROVIDER_FAILED, err)

            try:
                proposal = parse_proposal(output)
            except ProposalError as exc:
                malformed += 1
                res.steps.append({"kind": "malformed", "error": exc.code})
                self._audit.append("proposal_rejected", run_id=run_id, error=exc.code)
                if malformed > cfg.max_malformed:
                    return self._end(res, RunState.MALFORMED_OUTPUT, exc.code)
                transcript.append({"role": "observation", "status": "malformed_output", "reason": exc.code})
                continue
            malformed = 0

            if isinstance(proposal, FinalProposal):
                res.steps.append({"kind": "final"})
                return self._end(res, RunState.COMPLETED, "final_answer", answer=proposal.answer)

            op = operation_id_for(run_id, proposal)
            digest = args_sha256(proposal.args)
            transcript.append({"role": "assistant", "capability": proposal.capability,
                               "args": proposal.args, "operation_id": op})
            self._audit.append("proposal", run_id=run_id, operation_id=op,
                               capability=proposal.capability, args_sha256=digest)
            outcome = self._gw.submit(operation_id=op, capability=proposal.capability,
                                      args=proposal.args, approval_token=approvals.get(op))
            res.steps.append({"kind": "action", "operation_id": op, "capability": proposal.capability,
                              "status": outcome.status.value, "reason": outcome.reason})

            if outcome.status in _CONTINUE:
                transcript.append(self._observe(outcome))
                continue
            if outcome.status is Status.APPROVAL_REQUIRED:
                pending = PendingApproval(op, self._gw.actor, proposal.capability, proposal.args,
                                          digest, outcome.reason)
                return self._end(res, RunState.AWAITING_APPROVAL, outcome.reason, pending=pending)
            if outcome.status in _TERMINAL:
                return self._end(res, _TERMINAL[outcome.status], outcome.reason)
            if outcome.status in _BLOCKED:
                blocked += 1
                if blocked > cfg.max_blocked:
                    return self._end(res, RunState.BLOCKED_LIMIT, outcome.reason)
                transcript.append(self._observe(outcome))
                continue
            raise AssertionError(f"unmapped status {outcome.status}")  # pragma: no cover
