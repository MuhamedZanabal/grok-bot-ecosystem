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

  Since cycle-004 every worker runs inside a bubblewrap sandbox (see below).
- **Argument schemas.** Every bound capability declares a JSON-Schema-subset `args_schema`,
  enforced before execution. It is stricter than JSON Schema: missing `additionalProperties`
  means false, `pattern` is a full match, and unknown keywords are rejected.
- **Typed tool registry + MCP adapter.** `ToolRegistry.import_mcp(server, tools_list_payload)`
  maps MCP `tools/list` definitions to `cap.mcp_<server>.<name>` as **candidates**: not
  registered with policy, not executable, proposed at risk 3 regardless of annotations.
  `accept()` (operator, explicit risk, worker, reason) makes a candidate executable; a grant is
  still required. There is no MCP transport (no network).

## cycle-004-isolation: OS worker sandbox (`src/tools/sandbox.py`, Linux + bubblewrap)

- **Sandbox.** Every `SubprocessHandler` call runs `bwrap` with fresh user, PID, network,
  IPC, UTS (and, if available, cgroup) namespaces; `--cap-drop ALL`, `--new-session`,
  `--die-with-parent`, `--clearenv`. The filesystem is read-only: `/usr` (plus merged-usr
  symlinks), the Python installation, `/worker/worker.py` and the tool root at `/tool`, with a
  private tmpfs at `/tmp`. There is no network (not even the host loopback), no host `/proc`,
  and no parent state files. The PID namespace kills `setsid`/double-forked descendants.
- **Fail closed.** If `/usr/bin/bwrap` is missing, not root-owned, writable by others, or its
  probe fails, `SandboxUnavailable` is raised and no tool runs. There is no fallback.
  Requires `bubblewrap` and unprivileged user namespaces (`apt-get install bubblewrap`; on
  Ubuntu 24.04 AppArmor may need `kernel.apparmor_restrict_unprivileged_userns=0`; see D-0013).
- **Enforced host hardening.** `ControlPlane()` sets `PR_SET_DUMPABLE=0` and raises
  `HostHardeningError` if that fails (`require_host_hardening=False` is an explicit, audited
  opt-out).
- **Persistent registry.** `ToolRegistry(..., store=RegistryStore("registry.sqlite"))` versions
  every trust change (`tool_events`). After a restart it re-binds accepted tools from their
  stored, re-validated `WorkerSpec`; candidates stay inert.

## cycle-005-browser-egress: audited egress + `cap.browser.fetch`

- **Egress (deny by default).** Workers keep `--unshare-net`. A `WorkerSpec` with
  `egress=("host:port", ...)` (exact, lowercase, no wildcards) gets one Unix socket at
  `/run/egress/sock`, served by a host-side `EgressProxy` for that call only.
  - The worker sends `CONNECT host:port\n`. The proxy enforces a strict grammar, the
    allowlist *before* any DNS lookup (so no DNS exfiltration), a rebinding guard (names must
    resolve to global IPs), and per-call connection and byte budgets.
  - Every attempt is audited (`egress_allowed`, `egress_denied`, `egress_failed`,
    `egress_closed`).
  - Egress requires risk ≥ EXTERNAL (so it is approval-gated) and an audit sink.
- **Fork limit.** `RLIMIT_NPROC` (`max_procs`, default 32) is set inside the sandbox's user
  namespace.
- **`cap.browser.fetch`** (`tools.register_browser_fetch(registry, ("host:port",), by=...)`):
  a stdlib GET with title, text and link extraction. Every redirect hop goes through the
  proxy. TLS is verified. No cookies, credentials or JavaScript. Page content is untrusted data.

## cycle-006-memory: provenance-tagged memory (`src/memory/`)

- `MemoryStore(path, key)` (SQLite, FTS5 when available, otherwise LIKE). Every record carries
  `source_id`, the exact `origin_url`, `source_ref`, `trust` (`untrusted` by default),
  `content_sha256`, `created_at`/`expires_at`, and an HMAC chain (`prev_mac`, `mac`).
- Secrets are redacted before hashing. A URL containing a secret is refused. Dedup is by
  content hash. There are size, count and TTL budgets. Hash and MAC are verified on every
  read; tampered rows are excluded and audited.
- `register_memory(policy, cp, store, audit)` binds `cap.memory.write` (risk 1, idempotent)
  and `cap.memory.search` (risk 0) as `TrustedHostHandler`s. Results come back as
  `{"type": "untrusted_data", "provenance": {...}, "content": ...}`.
- `memory.ingest_fetch(cp, actor, fetch_op_id, fetch_result)` stores `cap.browser.fetch`
  results as untrusted records through the control plane.

## cycle-007-research: research/evidence engine (`src/research/`)

- `SourceRegistry`:
  - Stores the URL byte-exact; `source_id = "src:" + sha256(exact URL)[:32]`, with no normalization.
  - Every source is `candidate` with `trusted=0` and `execution_allowed=0` (SQL CHECK constraints).
  - Lifecycle is `candidate→evaluating→accepted|rejected|deferred`, plus `deferred→evaluating`. Each step needs evidence and is audited.
- `ClaimLedger` + `Verifier`:
  - A claim cites `{source_id, record_id, content_sha256, quote}`.
  - The verifier refuses claims with fabricated, mutated or evidence-free citations.
  - On every read, a claim with stale, contradicted, tampered or rejected-source evidence becomes `UNKNOWN`.
- `ResearchRun`:
  - Every write goes through ControlPlane: `cap.research.source_register`, `cap.research.source_transition`, `cap.research.claim_record`, `cap.browser.fetch`, `cap.memory.write` and `cap.memory.get`.
  - Extraction is Tier 0 deterministic first, with a Tier 1 model (MockProvider only) as data.
  - `branch_closed` fires after 3 consecutive passes with no superior candidate on the recorded metric.
  - `write_report` emits a byte-deterministic, cited `report.json` and `report.md`.

```bash
python3 -m unittest discover -s tests -t . -v     # invariant + adversarial tests
bash scripts/mutation_check.sh                     # each disabled guard must fail the suite
python3 bench/bench_control_plane.py 200           # execute() overhead
python3 bench/bench_orchestrator.py 100            # agent run overhead (mock provider)
python3 bench/bench_tools.py 50                    # sandboxed tool call overhead
python3 bench/bench_browser.py 30                  # browser fetch via egress proxy (local fixture)
python3 bench/bench_memory.py 200                  # memory write/search via the control plane
python3 bench/bench_research.py 50                 # one research pass + report (local fixture)
MUTANT_ONLY='sandbox' bash scripts/mutation_check.sh  # local iteration on a subset
PYTHONPATH=src python3 -m control_plane kill engage .state/KILL --reason "operator stop"
PYTHONPATH=src python3 -m control_plane audit-verify .state/audit.jsonl
```

Governance artifacts: `ecosystem_manifest.json`, `capability_graph.json`, `decision_log.md`,
`rejection_log.md`, `integration_log.md`, `benchmark_log.md`, `security_log.md`, `roadmap.md`.
