# Decision log (append-only)

## D-0001: Define Cycle 1 (control plane and bounded-loop governance)
- **decision_id:** D-0001
- **date:** 2026-10-19
- **problem:** New, empty repository (baseline `b7fa4c351da1bc89983346ee5ae8f23fbf6b7920`: `.gitignore`, `README.md`; no CI, PRs, dependencies or security controls). Nothing may run with authority until a gate exists that enforces deny-by-default, approvals, idempotency, budgets, kill switch and audit.
- **options:** (a) follow default order, Cycle 1 = control plane; (b) start with orchestration (Cycle 2) and add governance later; (c) insert a revenue sub-cycle.
- **evidence:** baseline inspection in `ecosystem_manifest.json#baseline_inspection` (`gh pr list --state all` and `gh run list` both empty; `git ls-files` = 2 files). No cost or revenue telemetry exists, so (c) has no evidence to justify reordering.
- **selected:** (a). Cycle definition:
  - cycle_id: `cycle-001-control-plane`
  - objective: one gate (`ControlPlane.execute`) through which every action must pass, resolving to a deterministic status.
  - capability area: `cap.control.*`
  - budgets: time 1 agent session; tokens UNKNOWN (no metering); repository: one branch, one PR, <= ~1,000 lines production code; implementation: stdlib only, zero runtime dependencies; verification: unittest + adversarial set + mutation check + CI on 3.11/3.12/3.13.
  - success metric: 100% of policy-invariant tests pass locally and in CI, **and** 0 unauthorized executions across an adversarial set of >= 20 cases, **and** every disabled guard in the mutation check fails the suite.
  - expected_value: high (prerequisite for every later cycle); qualitative, not measured.
  - estimated_cost: ESTIMATE one agent session; dollar/token cost UNKNOWN.
  - expected_revenue: 0 (prerequisite cycle).
  - risk: low (no external side effects; new private repo).
  - confidence: high for the in-process guarantees tested; low for anything not tested (see remaining gaps).
- **rejected:** (b) building agents before the gate exists would mean granting authority without enforcement; (c) no telemetry to evidence it.
- **tradeoffs:** in-process library, not a service; grants live in memory; one process per ledger file is the tested configuration.
- **reversal criteria:** if Cycle 2 shows the in-process gate cannot be enforced (e.g. agents need out-of-process tools), promote the control plane to a separate process with the same API and tests.

## D-0002: Stdlib-only implementation with SQLite ledger and HMAC tokens
- **decision_id:** D-0002
- **date:** 2026-10-19
- **problem:** Choose storage and token primitives for idempotency and approvals.
- **options:** ledger: SQLite / append-only JSONL / Redis. tokens: HMAC-SHA256 / Ed25519 signatures / JWT library.
- **evidence:** `src/control_plane/ledger.py` uses `INSERT OR IGNORE` on a primary key, giving atomic claim; `tests/test_idempotency.py::test_concurrent_duplicates_execute_once` (8 threads, 1 execution). `src/control_plane/approvals.py` uses `hmac.compare_digest`.
- **selected:** SQLite (WAL, `synchronous=FULL`) and HMAC-SHA256 (stdlib).
- **rejected:** JSONL ledger (no atomic claim without extra locking); Redis (new service and dependency); Ed25519 (no stdlib implementation); JWT library (third-party, algorithm-confusion history).
- **tradeoffs:** HMAC is symmetric, so whoever can verify can also issue. Key custody is a Cycle 9 item.
- **reversal criteria:** multi-host deployment, or a need for verifiers that cannot issue, leads to asymmetric signatures and a shared ledger service.

## D-0003: Approvals are single-use by binding to operation_id
- **decision_id:** D-0003
- **date:** 2026-10-19
- **problem:** Approvals must not be replayable for a second execution.
- **options:** (a) track consumed token ids; (b) bind the token to operation_id and rely on the at-most-once ledger.
- **evidence:** `tests/test_approvals.py::test_approval_executes_once` (EXECUTED then REPLAYED, handler called once); adversarial case "L3 token reused for new op" -> `approval_wrong_scope`.
- **selected:** (b), with no extra state.
- **rejected:** (a) adds a second store that must stay consistent with the ledger.
- **tradeoffs:** the ledger must be durable; losing it would re-enable an unexpired token (max TTL 1h).
- **reversal criteria:** if ledgers become sharded or expirable, add an explicit consumed-token table.

## D-0004: Define Cycle 2 (model-neutral agent orchestration substrate)
- **decision_id:** D-0004
- **date:** 2026-10-19
- **problem:** Cycle 1 (merged to `main` at `7edabd1e8efe6794446c9a9a41256c8088f0decc`, PR #1) provides a gate but no agent loop. Its two open gaps block safe orchestration: grants are in memory only, and nothing structurally stops an agent from calling handlers directly (security_log.md S-0001 gaps 4 and 5).
- **options:** (a) adopt an agent framework; (b) a stdlib runner whose only output channel is typed proposals submitted through `ControlPlane.execute`; (c) defer orchestration and do security hardening first.
- **evidence:** `roadmap.md` RM-0001 orders Cycle 2 next; `security_log.md` S-0001 lists gaps 4 and 5 as Cycle 2 prerequisites.
- **selected:** (b). Cycle definition, recorded before any Cycle 2 code:
  - cycle_id: `cycle-002-orchestration`
  - objective: models sit behind a provider-neutral, text-in/text-out interface. Their output is parsed into typed proposals that can only reach handlers via an actor-bound gateway onto `ControlPlane.execute`. Grants become persistent and versioned, and every grant and revoke is audited.
  - capability area: `cap.orchestration.agent_runtime`, `cap.control.persistent_policy_store`
  - budgets: time 1 agent session; tokens UNKNOWN (no metering); repository: one branch `cycle-2/orchestration`, one PR, <= ~800 new production lines; implementation: stdlib only, no real model API calls, no secrets; verification: unittest, extended adversarial set, extended mutation check, CI 3.11/3.12/3.13.
  - success metric (all must hold, locally and in CI):
    1. 100% of tests pass.
    2. 0 unauthorized handler executions across >= 15 adversarial *model-output* cases (Cycle 1's 20 control-plane cases must still give 0).
    3. Duplicate proposals execute each handler at most once.
    4. Provider timeout, outage and malformed output each end in a documented terminal run state.
    5. Grants survive a process restart and carry a monotonically increasing version, and every grant and revoke has an audit record.
    6. Every new mutant in `scripts/mutation_check.sh` is killed.
  - expected_value: high; this is a prerequisite for Cycles 3-10 (qualitative judgement, not measured).
  - estimated_cost: ESTIMATE one agent session; token/$ cost UNKNOWN.
  - expected_revenue: 0 (prerequisite cycle).
  - risk: low (no external side effects; mock provider only).
  - confidence: medium-high for tested properties. Isolation within one Python process is a boundary of data flow, not of memory (see S-0003).
- **rejected:** (a) external code is untrusted and the spec asks for dependency-light work (see R-0003); (c) leaves gaps 4 and 5 open, and hardening without a runner has nothing to harden.
- **tradeoffs:** a run is resumed by re-running with the same run_id and approvals, which relies on ledger idempotency rather than a persisted run state machine (that is Cycle 7). Each provider call is charged at a configured estimate, not metered.
- **reversal criteria:** if real providers need streaming or tool-calling formats that do not fit text-in/text-out, add an adapter layer that still emits the same `Proposal` type. If cross-process isolation becomes required (Cycle 3 handlers), move handlers out of process behind the same gateway API.

## D-0005: Merge PR #1 with a merge commit (not squash)
- **decision_id:** D-0005
- **date:** 2026-10-19
- **problem:** How to land Cycle 1 on `main`.
- **options:** squash, rebase, merge commit.
- **evidence:** Cycle 1 artifacts cite commit SHAs `2784633`, `560de62`, `7c6a998` as evidence pointers. Squash or rebase would drop those commits from `main` history. `gh pr checks 1` was green, then `gh pr merge 1 --merge --match-head-commit 7c6a998...` produced merge commit `7edabd1e8efe6794446c9a9a41256c8088f0decc`.
- **selected:** merge commit; the head was pinned so an unreviewed push could not slip in.
- **rejected:** squash and rebase, because they break evidence pointers.
- **tradeoffs:** a slightly noisier history.
- **reversal criteria:** if the logs stop citing intermediate SHAs, squash becomes acceptable.

## D-0006: Model interface is text-in/text-out; proposals are the only output channel
- **decision_id:** D-0006
- **date:** 2026-10-19
- **problem:** Make orchestration model-neutral and ensure a model can never hold or invoke a handler.
- **options:** (a) pass tool objects or callables to the model adapter (framework style); (b) give the adapter a canonical-JSON string and accept back a string that a strict parser turns into `ActionProposal`/`FinalProposal`; (c) vendor-native tool-calling schemas.
- **evidence:** `src/orchestrator/provider.py` (`complete(request_json: str) -> str`). `tests/test_orchestrator_adversarial.py::HandlerIsolationTests::test_provider_receives_only_a_json_string_with_no_callables`. `ORCH_ADVERSARIAL cases=18 unauthorized_executions=0`.
- **selected:** (b). The runner holds only an `ActorGateway` (`AgentRunner` raises `TypeError` for anything else).
- **rejected:** (a), because it hands authority to untrusted code; (c), deferred as vendor-specific (R-0004).
- **tradeoffs:** native tool-calling reliability gains are not used; adapters must translate.
- **reversal criteria:** a real provider adapter in a later cycle shows a measurable parse-failure rate that native tool calling would remove. Native calls must still terminate in the same `Proposal` types.

## D-0007: Deterministic operation ids; resume by re-run
- **decision_id:** D-0007
- **date:** 2026-10-19
- **problem:** Duplicate proposals, re-runs and post-approval resumes must not re-execute actions.
- **options:** (a) random operation id per proposal; (b) `run:<run_id>:<sha256(capability, args, idempotency_key)[:32]>`; (c) a persisted run state machine.
- **evidence:** `src/orchestrator/runner.py::operation_id_for`. `tests/test_runner.py::test_duplicate_proposals_execute_once` (4 identical proposals give 1 execution and 3 replays), `test_rerun_same_run_id_replays_instead_of_reexecuting`, and `test_approval_required_pauses_and_resume_completes`. Mutant `random_operation_id` is killed.
- **selected:** (b). Models that need a repeated identical action supply distinct `idempotency_key`s (`test_distinct_idempotency_keys_are_distinct_operations`).
- **rejected:** (a), which breaks dedup; (c), deferred to Cycle 7 (workflow engine).
- **tradeoffs:** resume relies on the model reproducing the same proposals. A non-deterministic real model may diverge on re-run, in which case new proposals simply need new approvals.
- **reversal criteria:** Cycle 7 introduces persisted run state; then resume continues from stored state instead of re-running.

## D-0008: Define Cycle 3 (typed tool + MCP execution substrate)
- **decision_id:** D-0008
- **date:** 2026-10-19
- **problem:** After Cycle 2 (PR #2 merged to `main` at `27ac19430bbe25651cac2383d3063d8dbf0a297b`), handlers still run inside the control-plane process. That leaves two open gaps: tool code can reach other handlers and secrets (security_log.md S-0003 gap 1), and tool arguments are only checked for JSON-ness, never against a per-capability schema. There is also no way to describe tools in a typed registry, or to ingest external (MCP) tool definitions without trusting them.
- **options:** (a) an in-process sandbox (restricted builtins); (b) one subprocess worker per call with a narrow JSON protocol, an explicit env allowlist, process-group kill on timeout and rlimits; (c) containers or namespaces; (d) adopt an MCP SDK.
- **evidence:** S-0003 gap 1 and RM-0002 scope Cycle 3 to out-of-process handlers plus schema validation. Python has no reliable in-process sandbox; restricted builtins are known to be escapable, so (a) is rejected without benchmark.
- **selected:** (b). Cycle definition, recorded before any Cycle 3 code:
  - cycle_id: `cycle-003-tool-substrate`
  - objective: (1) tool code runs only in a separate worker process with no inherited secrets, file descriptors or parent objects, and is killed with its process group on timeout; (2) every capability declares an argument schema in a defined JSON-Schema subset, enforced before execution; (3) a typed tool registry plus an MCP `tools/list` descriptor adapter that imports external tools as *candidate/untrusted* with `execution_allowed=false`, unregistered with policy, until an operator explicitly accepts them. Execution after that still needs a grant.
  - capability area: `cap.tools.handler_isolation`, `cap.tools.args_schema`, `cap.tools.registry`, `cap.tools.mcp_adapter`
  - budgets: time 1 agent session; tokens UNKNOWN; repository: one branch `cycle-3/tool-substrate`, one PR, <= ~900 new production lines; implementation: stdlib only, no network, no real MCP server connections; verification: unittest, extended adversarial set, extended mutation check, CI 3.11/3.12/3.13.
  - success metric (all must hold, locally and in CI):
    1. 100% of tests pass.
    2. A timed-out worker *and its child process* are verified dead after timeout.
    3. A canary secret in the parent environment is absent from the worker environment, and the worker inherits no parent ledger/audit/grant file descriptors.
    4. Tool code executing in the worker cannot import or locate `control_plane`/`orchestrator` objects; the pinned in-process gap test is flipped to assert this.
    5. 0 unauthorized executions across >= 12 new tool-substrate adversarial cases (schema bypass, candidate MCP tool execution, risk-hint downgrade, env exfiltration, worker protocol abuse); prior sets stay at 20/0 and 18/0.
    6. Every new mutant is killed.
  - expected_value: high; tools are how agents act, and nothing past Cycle 3 is safe without isolation (qualitative judgement).
  - estimated_cost: ESTIMATE one agent session; token/$ UNKNOWN. Added per-call latency from process spawn: UNKNOWN until measured (B-0005).
  - expected_revenue: 0 (prerequisite cycle).
  - risk: low-medium. Subprocess management is new failure surface (zombie or orphan processes, platform differences: Linux-only rlimit and process-group semantics are assumed).
  - confidence: medium. The worker runs as the *same OS user*, so this is process isolation, not a security sandbox (see S-0005).
- **rejected:** (a) not a real boundary; (c) needs privileges and infrastructure not available or verified in CI (UNKNOWN), deferred to Cycle 9; (d) third-party code and network transport are out of scope, deferred (R-0006).
- **tradeoffs:** process spawn per call costs latency; there are no warm workers. Schema subset semantics are deliberately stricter than JSON Schema (missing `additionalProperties` means false; `pattern` uses full match; unknown keywords are rejected).
- **reversal criteria:** spawn latency dominates real workloads (> 50 ms p50 overhead) leads to a pooled worker with per-call reset. A need for real isolation from same-UID processes leads to uid/namespace/seccomp sandboxing in Cycle 9.

## D-0009: One worker process per call, launched with `python -I -S`
- **decision_id:** D-0009
- **date:** 2026-10-19
- **problem:** How to run tool code out of process.
- **options:** (a) a fresh process per call; (b) a pooled warm worker; (c) `multiprocessing` (fork).
- **evidence:** `src/tools/subprocess_handler.py`; `tests/test_subprocess_handler.py::WorkerIsolationTests` (env = `{PATH, LANG}`, no parent ledger/audit/grant fds, `importable=[]`, `loaded=[]`, `gc_hits=0`); `test_timeout_kills_worker_and_its_children`. Cost: `python3 bench/bench_tools.py 50` gave `p50_ms=24.810` against in-process `execute p50_ms=0.719` (B-0005).
- **selected:** (a). `-I -S` (isolated mode, no site), `start_new_session`, `close_fds`, empty temp cwd, allowlisted env, rlimits set inside the worker, and SIGKILL of the whole process group on deadline. The worker only imports modules physically under `tool_root`.
- **rejected:** (b) state leaks between calls and needs a reset protocol, so it is deferred until latency matters; (c) fork copies the parent's memory (secrets, objects) into the tool process, which defeats the purpose.
- **tradeoffs:** about 24 ms per call. A grandchild that calls `setsid()` escapes the process-group kill (S-0005).
- **reversal criteria:** p50 spawn overhead becomes significant relative to real tool latency in Cycle 4+ workloads, leading to a pool with fresh-process-per-tenant semantics.

## D-0010: Strict default `ControlPlane(allow_inprocess_handlers=False)`; schemas required to bind
- **decision_id:** D-0010
- **date:** 2026-10-19
- **problem:** Make isolation and validation the default, not an option.
- **options:** opt-in isolation, or opt-out (explicit flag for in-process).
- **evidence:** `src/control_plane/executor.py::bind` refuses non-`IsolatedHandler`s unless the flag is set, and refuses any capability without `args_schema`. `tests/test_schema.py::test_default_control_plane_is_strict`; mutants `inprocess_allowed_by_default` and `skip_schema_enforcement` are killed. Every bind is audited with `isolated: true/false`.
- **selected:** opt-out. Legacy Cycle 1/2 tests use in-process counters via an explicit `Harness(inprocess=True)`, which sets the flag.
- **rejected:** opt-in, which contradicts default-deny.
- **tradeoffs:** callers must write a schema for every capability.
- **reversal criteria:** none expected.

## D-0011: MCP tools enter as candidates at risk 3; annotations never lower risk
- **decision_id:** D-0011
- **date:** 2026-10-19
- **problem:** External tool definitions are untrusted (names, schemas, descriptions and hints).
- **options:** (a) trust annotations (`readOnlyHint`) to set risk; (b) import as executable at a default risk; (c) import as non-executable candidates proposed at risk 3, which an operator must accept with an explicit risk, worker and reason.
- **evidence:** `src/tools/registry.py`, `src/tools/mcp_adapter.py`; `tests/test_tool_registry.py`; `tests/test_tool_adversarial.py` (`mcp readOnlyHint downgrade` gives `approval_required`; `mcp candidate execute`/`grant` are refused). Mutants `mcp_default_risk_read` and `mcp_import_as_accepted` are killed.
- **selected:** (c). Candidates are not registered with policy, so they cannot be granted, bound or executed.
- **rejected:** (a), because a malicious server controls the hints; (b), which skips human review.
- **tradeoffs:** every MCP tool needs an operator decision. Registry trust state is in memory (only the audit trail persists).
- **reversal criteria:** a signed/attested server allowlist (Cycle 9) could justify pre-accepting tools from specific servers.

## D-0012: Reorder. Insert security sub-cycle `cycle-004-isolation` before browser/computer use
- **decision_id:** D-0012
- **date:** 2026-10-20
- **problem:** The next default cycle (browser/computer use) will handle credentials and external accounts. RM-0003's reversal criterion says any cycle that gives tools real credentials must first close S-0005 gaps 1-2. Two more gaps (3: `setsid` escapes the kill; 5: registry trust state not persisted) undermine the same boundary.
- **options:** (a) proceed with browser/computer use and carry the gaps; (b) pull the Cycle 9 isolation prerequisites forward as a bounded sub-cycle; (c) do all of Cycle 9 now.
- **evidence:**
  - `security_log.md` S-0005 open gaps 1-5, and `roadmap.md` RM-0003's reversal criterion.
  - Tool-substrate CI run 39325010443 / `tests/test_subprocess_handler.py` shows the worker runs with the agent's UID and filesystem.
  - `HARDENING unhardened={... 'canary': True}` showed a real same-UID `/proc/<ppid>/environ` leak when the host does not opt in.
  - Mechanisms available on the box (probed 2026-10-20):
    - `which bwrap unshare setpriv` gave `/usr/bin/bwrap` (`bubblewrap 0.12.0`), `/usr/bin/unshare` and `/usr/bin/setpriv`.
    - `unshare --user --map-root-user --net --mount --pid --fork true` gave `unshare userns OK`.
    - A bwrap probe with `--unshare-all --ro-bind /usr /usr --tmpfs /tmp` gave `home exists False workspace False`, `write /usr: OSError`, `tmp write ok`, `net: OSError 101` (network unreachable) and `pid 2 ppid 1` (own PID namespace).
  - GitHub Actions `ubuntu-latest`: UNKNOWN until probed in CI (bubblewrap preinstalled? AppArmor `kernel.apparmor_restrict_unprivileged_userns`?).
- **selected:** (b). Cycle definition, recorded before code:
  - cycle_id: `cycle-004-isolation` (security sub-cycle; browser/computer use becomes cycle-005, and later cycles shift by one; see RM-0004)
  - objective: tool workers run inside an OS sandbox (bubblewrap: user, PID, network, mount, IPC, UTS and cgroup namespaces) with:
    - no network;
    - a read-only filesystem limited to the interpreter, the stdlib and the tool root, plus a private writable tmpfs;
    - no view of parent state files, the home directory or the workspace;
    - PID-namespace teardown, so detached (`setsid`) descendants die with the worker.

    If the sandbox is unavailable the handler refuses to run (fail closed), and there is no silent fallback. In addition, `ControlPlane` construction enforces host non-dumpability, and registry trust state persists in a versioned, audited SQLite store.
  - capability area: `cap.tools.os_sandbox`, `cap.control.host_hardening`, `cap.tools.registry` (persistence)
  - budgets: time 1 agent session; tokens UNKNOWN; repository: one branch `cycle-4/isolation`, one PR, <= ~600 new production lines; implementation: stdlib plus the system `bwrap` binary (justified in D-0013); verification: unittest, a sandbox adversarial set, an extended mutation check, CI 3.11/3.12/3.13 with the sandbox actually active.
  - success metric (all must hold, locally and in CI):
    1. 100% of tests pass with the sandbox enforced, with no skips of sandbox tests in CI.
    2. 0 escapes across >= 12 sandbox adversarial cases (read ledger/grant/audit DBs, home, workspace; write outside tmp; network sockets, including the host loopback and a host Unix socket; `setsid` escape of the timeout; parent `/proc` environ), and prior adversarial sets stay at 20/0, 18/0 and 20/0.
    3. Zero host-visible descendant processes after a timeout of a tool that spawned a `setsid` child.
    4. `ControlPlane()` leaves the host non-dumpable, or raises.
    5. Registry trust state and accepted tools survive a restart, with versioned, audited events.
    6. Every new mutant is killed.
  - expected_value: high; it unblocks credential-bearing cycles (qualitative).
  - estimated_cost: ESTIMATE one agent session; token/$ UNKNOWN; added per-call latency UNKNOWN until measured.
  - expected_revenue: 0.
  - risk: medium. CI runner sandbox support is UNKNOWN until probed, and host-configuration differences may make the sandbox unavailable, in which case tools refuse to run.
  - confidence: medium.
- **rejected:** (a) violates RM-0003; (c) is too large for one bounded cycle (key custody, audit anchoring and content filtering stay in the later security-hardening cycle).
- **tradeoffs:** the browser cycle is delayed by one cycle, and there is a Linux plus bubblewrap platform dependency.
- **reversal criteria:** if bubblewrap proves unavailable or unreliable on target hosts, switch to a dedicated low-privilege UID with `setpriv` plus `unshare` (needs root, so it is deployment-specific), keeping the same `SubprocessHandler` API and the same adversarial suite.

## D-0013: bubblewrap as the worker sandbox; CI system dependency
- **decision_id:** D-0013
- **date:** 2026-10-20
- **problem:** Choose the OS isolation mechanism for cycle-004 (D-0012 scope (a) and (d)) that exists on this box and on GitHub `ubuntu-latest`, and justify the new apt dependency.
- **options:** (a) `bwrap` (bubblewrap) with unprivileged user namespaces; (b) a dedicated low-privilege UID via `setpriv`/`sudo` plus `unshare`; (c) the `unshare` CLI alone; (d) Docker/podman; (e) a setuid helper of our own. Alternatives are recorded in R-0009.
- **evidence:**
  - Box: `/usr/bin/bwrap` is `bubblewrap 0.12.0`, root-owned, and unprivileged userns works (D-0012 probe).
  - Implementation `src/tools/sandbox.py`: user/PID/net/IPC/UTS namespaces (cgroup `-try`), `--cap-drop ALL`, `--new-session`, `--die-with-parent`, `--clearenv`, ro `/usr` and python prefix, ro `/worker/worker.py` and `/tool`, tmpfs `/tmp`, `--remount-ro /`.
  - Sandboxed probe output: env `{LANG, PATH, PWD=/tmp}`, cwd `/tmp`, `sys.path` `['/tool', '/usr/lib/python313.zip', '/usr/lib/python3.13', '/usr/lib/python3.13/lib-dynload']`, fds `{0 pipe, 1 /dev/null, 2 /dev/null, 3 pipe}`.
  - `python3 -m unittest tests.test_sandbox_adversarial` gave `SANDBOX_ADVERSARIAL cases=21 escapes=0`.
  - Fail closed: `tests/test_sandbox.py` (missing bwrap gives `bwrap_not_found_or_untrusted` and `SubprocessHandler()` raises; a user-owned 0777 copy is refused; a failing probe gives `probe_failed:rc=`).
  - The first 3.11 run (uv Python under `/home/box/.local/share/uv/...`) failed closed with `probe_failed:rc=1 ... AssertionError`, because the original probe demanded an empty `/home` while the interpreter itself is mounted from there. The probe now checks that every directory above an extra (Python) mount lists only the path to that mount, that the host checkout is absent, and that the network is unreachable. After that, 3.11, 3.12 and 3.13 all gave `Ran 146 tests ... OK` and `SANDBOX_ADVERSARIAL cases=21 escapes=0`.
  - GitHub runner: bubblewrap presence and AppArmor userns policy were UNKNOWN before this PR's CI run. The CI step prints `SANDBOX_CI_PROBE=...` (recorded in I-0012).
- **selected:** (a). The CI dependency is `apt-get install -y --no-install-recommends bubblewrap` (Ubuntu archive package, signed by the distribution; no third-party repo or PPA). If the unprivileged-userns probe fails, CI sets `kernel.apparmor_restrict_unprivileged_userns=0` **on the ephemeral runner only**. This is a CI host-configuration change, not a code path; production hosts must allow userns for bwrap, or tools fail closed.
- **rejected:** see R-0009.
- **tradeoffs:**
  - Linux-only; non-Linux hosts cannot run tools at all (fail closed).
  - The interpreter prefix is mounted read-only, so the worker can read the stdlib and site-packages of that interpreter (no secrets expected there; `-S` keeps site-packages off `sys.path`).
  - Relaxing the AppArmor sysctl on CI weakens that runner's kernel hardening for the job's lifetime.
  - No seccomp filter yet.
- **reversal criteria:** bwrap unavailable or unreliable on target hosts → (b) behind the same `SubprocessHandler` API and adversarial suite. A bwrap CVE affecting non-setuid mode → pin or patch it before any credential-bearing cycle.

## D-0014: Host hardening enforced at ControlPlane construction; registry trust persisted
- **decision_id:** D-0014
- **date:** 2026-10-20
- **problem:** D-0012 scope (b) and (c), from S-0005 gaps 2 and 5.
- **options:**
  - (b1) Keep it opt-in; (b2) enforce it in `ControlPlane.__init__` and raise if it fails, with an explicit opt-out; (b3) enforce it with no opt-out.
  - (c1) Persist only audit records; (c2) a SQLite store with an append-only `tool_events` table plus a current-state `tools` table, written in one transaction.
- **evidence:**
  - `tests/test_subprocess_handler.py::HostHardeningTests::test_control_plane_construction_enforces_non_dumpable` gave `HARDENING control_plane_construction={'before': False, 'after': True}`.
  - `test_control_plane_refuses_to_start_unhardened` gives `HostHardeningError` when `set_non_dumpable` returns False.
  - Mutants `host_hardening_optional` and `host_hardening_not_applied` were killed.
  - `tests/test_tool_registry.py::RegistryPersistenceTests`: state and version survive restart; an accepted tool re-binds and still needs a grant; candidates and rejected tools stay non-executable; history versions are strictly increasing (`tool_candidate` → `tool_accepted`); the audit records `registry_version` and `tool_reloaded`; a tampered `worker_json` (`module='../../evil'`) makes reload raise `ValueError`.
  - Mutants `registry_not_persisted` and `registry_reload_skips_validation` were killed.
- **selected:** (b2) and (c2). `require_host_hardening=False` exists for platforms without prctl. It is recorded in the audit (`host_hardening` event with `required=false`), never silent. `RegistryStore` defaults to `:memory:` for callers that don't pass a path; persistence requires a file path.
- **rejected:** (b1) is the gap itself. (b3) makes the control plane unconstructible on non-Linux even for policy-only use. (c1) cannot restore trust state.
- **tradeoffs:**
  - The process-wide non-dumpable flag makes the host undebuggable with ptrace and core dumps.
  - The registry file is not integrity-protected against a same-UID writer outside the sandbox. A tampered spec is still re-validated, but the trust flag can be flipped (that belongs to cycle-010 store authentication).
- **reversal criteria:** an operator needs ptrace on the host → use an opt-out per deployment, audited. Store tampering is observed → sign rows (cycle-010).

## D-0015: cycle-005 definition (browser substrate behind audited egress), recorded before code
- **decision_id:** D-0015
- **date:** 2026-10-20
- **problem:** RM-0005: the browser/computer-use substrate needs network access, but sandboxed workers have none (`--unshare-net`), and S-0006 gap 7 (no process-count limit) must close before credential-adjacent work.
- **options:**
  - Egress: (e1) a host-side filtering proxy reached through a Unix socket bound into the sandbox, with the worker keeping `--unshare-net`; (e2) share the host network namespace and filter with nftables or iptables; (e3) slirp4netns/pasta user-mode networking with its own filtering; (e4) an HTTP(S) forward proxy on host TCP loopback. Rejections are recorded in R-0011.
  - Browser: (b1) Playwright plus headless Chromium; (b2) CDP-direct against a system Chrome; (b3) a stdlib HTTP fetch plus `html.parser` tool.
  - Fork limit: (f1) `RLIMIT_NPROC` set inside the worker after it enters the user namespace; (f2) cgroup v2 `pids.max`.
- **evidence (probes, 2026-10-20 01:00-01:10 UTC+3):**
  - **RLIMIT_NPROC in a bwrap userns:**
    - A script under `bwrap --unshare-user --unshare-pid --unshare-net` doing `setrlimit(RLIMIT_NPROC,(16,16))` then forking in a loop gave `fork failed after 14 BlockingIOError 11` / `forked 14`.
    - Meanwhile the same host UID had `103` processes (`ps -u $(id -u) | wc -l`).
    - So on this kernel (6.12.94+, per-userns ucounts) the limit counts only processes in the worker's namespace, not the host UID's.
    - cgroup delegation on GitHub runners is UNKNOWN.
  - **Playwright:**
    - `pip download --no-deps playwright` (downloaded, not installed or executed) gave `playwright-1.64.0-py3-none-manylinux1_x86_64.whl`, 48 MB compressed and 136 MiB unpacked, 228 files.
    - It bundles `playwright/driver/node` (120 MiB, a Node.js runtime).
    - `License-Expression: Apache-2.0`. Requires `pyee`, `greenlet` (C extension).
    - Browsers are fetched separately by `playwright install` from Playwright's CDN.
    - Nothing was run.
  - **CDP-direct / system Chrome:**
    - `/usr/bin/google-chrome` is `Google Chrome 155.0.8059.84` (`/opt/google/chrome` is 443M; dpkg `Installed-Size: 452493`).
    - Inside our bwrap sandbox (`--unshare-net`, ro `/usr`, `/opt/google/chrome` and `/etc/fonts`, tmpfs `/tmp` and `/dev/shm`), `chrome --headless --no-sandbox --dump-dom data:...` rendered `<p id="x">js-ran</p>` (JS executed), rc 0, about 2 s wall, and 115 stderr lines (GPU init errors).
    - It needs `--no-sandbox` (Chrome's own sandbox is disabled; bwrap is the only boundary).
    - It cannot use a Unix-socket proxy directly, so it would need an in-sandbox TCP→Unix forwarder.
    - Chrome's own background network behaviour (updates, component fetches, telemetry) is UNKNOWN.
    - Whether Chrome is present on GitHub `ubuntu-latest` is UNKNOWN (not verified).
  - **stdlib fetcher:**
    - 0 new dependencies.
    - Per-call cost is about the existing sandbox spawn (B-0007: about 25 ms p50 locally).
    - It speaks HTTP over a socket the worker obtains from the proxy, so the Unix-socket egress design composes with no shim.
    - It does not execute JavaScript.
- **selected:**
  - **Egress (e1):** a host-side `EgressProxy` per tool call, listening on a Unix socket in a per-call directory that is bind-mounted read-only at `/run/egress`.
    - The worker sends one line, `CONNECT host:port`.
    - The proxy checks the capability's exact `host:port` allowlist (stored in the `WorkerSpec`) before any DNS resolution, so it never resolves or contacts non-allowlisted names (DNS exfiltration).
    - It resolves on the host and refuses non-global resolved addresses for name entries (rebinding guard).
    - It applies per-call connection and byte budgets, and audits every attempt (`egress_allowed`, `egress_denied`) plus each close (bytes).
    - Workers keep `--unshare-net`.
    - A capability with egress must have risk ≥ EXTERNAL (2), and egress without an audit sink fails closed.
  - **Fork limit (f1):** `RLIMIT_NPROC` set by `worker.py` inside the sandbox (WorkerSpec `max_procs`, default 32), plus a fork-bomb adversarial test.
  - **Browser (b3):** `cap.browser.fetch`, a repository-owned tool (`src/builtin_tools/browser.py`).
    - It does HTTP/HTTPS GET via the proxy and follows redirects by itself, so every hop is a new audited CONNECT and blocked hops are denied.
    - It extracts title, visible text and links with `html.parser`, with size, redirect and time caps.
    - No cookies, no credentials, no JavaScript.
  - b1 and b2 are deferred with the recorded evidence: no current capability needs JavaScript, and b1/b2 add 136 MiB to 443 MiB of third-party code, about 80× startup, a disabled inner Chrome sandbox and UNKNOWN background network behaviour.
- **cycle definition:**
  - cycle_id: `cycle-005-browser-egress`
  - objective: deny-by-default, audited, per-capability network egress for sandboxed workers; a process-count limit; and a sandboxed browser-fetch capability that can only reach allowlisted hosts.
  - capability area: `cap.tools.egress`, `cap.computer.browser` (fetch tier), `cap.tools.os_sandbox` (nproc).
  - budgets: time 1 agent session; tokens UNKNOWN; one branch `cycle-5/browser-egress`, one PR; ≤ ~700 new production lines; stdlib only (no new pip or apt dependency); verification by unittest, egress plus browser adversarial sets, an extended mutation check, and CI 3.11/3.12/3.13.
  - success metric (all must hold, locally and in CI):
    1. 100% of tests pass, with no skips.
    2. 0 unauthorized egress across ≥ 14 egress/browser adversarial cases (non-allowlisted host, wrong port, DNS exfil with 0 resolutions, redirect to a blocked host or port, CONNECT-line injection or malformation, rebinding guard, connection and byte budgets, a direct socket bypass, a capability without egress, a non-http scheme, a fork bomb).
    3. Every egress attempt (allowed or denied) appears in the audit log, and the hash chain verifies.
    4. The fork bomb is contained (bounded processes, worker terminates, 0 host-visible leftovers).
    5. `cap.browser.fetch` fetches a local fixture page through the proxy and extracts title, text and links.
    6. Prior adversarial sets stay at 20/0, 18/0, 20/0 and 21/0.
    7. Every new mutant is killed.
  - expected_value: medium-high (the first networked capability, under policy).
  - estimated_cost: ESTIMATE one agent session; token and dollar cost UNKNOWN.
  - expected_revenue: 0.
  - risk: medium. The proxy is new TCB code in the host process.
  - confidence: medium.
  - abort rule: if the metric is not met within budget, emit `cycle_aborted` with evidence instead of a partial claim.
- **rejected:** see R-0011 and R-0012.
- **tradeoffs:**
  - Allowlisted hosts can still receive any data the tool sends (exfiltration *to* an allowlisted host is policy-permitted).
  - No JavaScript rendering.
  - The proxy runs in the host process, so a proxy bug is a TCB bug.
- **reversal criteria:**
  - A capability needs JS rendering → b2 (system Chrome via CDP) behind an in-sandbox TCP→Unix forwarder to the same proxy, preceded by a security audit entry.
  - `RLIMIT_NPROC` is found to count host processes on a target kernel → cgroup `pids.max`.

## D-0016: Credential broker (designed, NOT implemented)
- **decision_id:** D-0016
- **date:** 2026-10-20
- **problem:** Future logged-in browsing needs credentials, and models must never see secrets.
- **options:** see RM-0006.
- **evidence:** current invariants: the env allowlist refuses credential-like names; audit redaction; sandboxed workers have no host files or network.
- **selected:** a design only (RM-0006). Nothing is implemented in this cycle.
- **rejected:** passing credentials through tool args, env or model context.
- **tradeoffs:** logged-in browsing is impossible until it is implemented.
- **reversal criteria:** n/a.
