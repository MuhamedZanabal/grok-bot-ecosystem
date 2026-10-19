# grok-bot-ecosystem

Governed agent ecosystem, built in bounded, evidence-verified cycles.

> Intelligence proposes. Policy authorizes. Deterministic infrastructure executes. Evidence verifies.

## Cycle 1: control plane (`src/control_plane/`, Python 3.11+, stdlib only)

Every action goes through `ControlPlane.execute(request, cost)`, which returns exactly one
deterministic `Status`:

| Step | Component | Blocks with |
|---|---|---|
| 1 | strict request schema (`executor.py`) | `rejected` |
| 2 | global kill switch, file-backed, fails closed (`killswitch.py`) | `killed` |
| 3 | capability policy, default DENY, explicit per-actor grants (`policy.py`) | `denied` |
| 4 | idempotency ledger, SQLite (`ledger.py`) | `replayed` / `conflict` / `in_doubt` |
| 5 | approval token for risk >= 2, HMAC-bound to actor + capability + operation_id + SHA-256(args) + expiry (`approvals.py`) | `approval_required` / `approval_invalid` |
| 6 | token-bucket rate limit (`ratelimit.py`) | `rate_limited` |
| 7 | bounded-loop governor: iteration, wall-clock and cost budgets (`governor.py`) | `cycle_aborted` / `killed` |
| 8 | handler run with timeout | `executed` / `failed` / `timed_out` |

Every decision is appended to a hash-chained JSONL audit log with secret redaction (`audit.py`).

Risk levels: 0 read, 1 reversible local write, 2 external/consequential, 3 financial/irreversible.

Grants are persistent and versioned (`grant_store.py`, SQLite). Every grant/revoke is an
immutable event with a monotonically increasing version and an audit record. A grant made
at one risk level does not cover the same capability re-registered at another.

## Cycle 2: orchestration (`src/orchestrator/`)

Models are untrusted text generators behind `ModelProvider.complete(request_json: str) -> str`.
The runner parses each output with a strict parser into a typed `ActionProposal` or
`FinalProposal` and submits actions through an actor-bound `ActorGateway`
(`ControlPlane.gateway(actor)`), never the control plane or a handler. Operation ids are derived
from `(run_id, capability, args, idempotency_key)`, so duplicate proposals replay instead of
re-executing, and a run can resume after approval by re-running with the same `run_id`.

Terminal run states: `completed`, `awaiting_approval`, `cycle_aborted`, `killed`,
`provider_failed`, `malformed_output`, `blocked_limit`, `in_doubt`.
Providers are tried in order with bounded attempts and per-call timeouts. Only the
deterministic `MockProvider` ships; no real model API is called.

## Cycle 3: tool substrate (`src/tools/`, `control_plane/schema.py`)

- **Out-of-process handlers.** `ControlPlane` binds only `IsolatedHandler`s by default
  (`allow_inprocess_handlers=False`). `SubprocessHandler(WorkerSpec(...))` runs each call in a
  fresh `python -I -S worker.py` with:
  - a new process group and an empty temp cwd;
  - `close_fds`;
  - an env built only from `PATH`/`LANG` plus an explicit allowlist (names that look like
    credentials are refused);
  - CPU, address-space and no-core rlimits;
  - a wall-clock deadline after which the whole process group is SIGKILLed.

  Call `tools.hardening.set_non_dumpable()` at host startup so same-UID workers cannot read the
  host's `/proc/<pid>/environ`.
- **Argument schemas.** Every bound capability declares a JSON-Schema-subset `args_schema`,
  enforced before execution. It is stricter than JSON Schema: missing `additionalProperties`
  means false, `pattern` is a full match, and unknown keywords are rejected.
- **Typed tool registry + MCP adapter.** `ToolRegistry.import_mcp(server, tools_list_payload)`
  maps MCP `tools/list` definitions to `cap.mcp_<server>.<name>` as **candidates**: not
  registered with policy, not executable, proposed at risk 3 regardless of annotations.
  `accept()` (operator, explicit risk, worker, reason) makes a candidate executable; a grant is
  still required. There is no MCP transport (no network).

```bash
python3 -m unittest discover -s tests -t . -v     # invariant + adversarial tests
bash scripts/mutation_check.sh                     # each disabled guard must fail the suite
python3 bench/bench_control_plane.py 200           # execute() overhead
python3 bench/bench_orchestrator.py 100            # agent run overhead (mock provider)
python3 bench/bench_tools.py 50                    # out-of-process tool call overhead
PYTHONPATH=src python3 -m control_plane kill engage .state/KILL --reason "operator stop"
PYTHONPATH=src python3 -m control_plane audit-verify .state/audit.jsonl
```

Governance artifacts: `ecosystem_manifest.json`, `capability_graph.json`, `decision_log.md`,
`rejection_log.md`, `integration_log.md`, `benchmark_log.md`, `security_log.md`, `roadmap.md`.
