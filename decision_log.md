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

## D-0017: Implementation choices made during cycle-005
- **decision_id:** D-0017
- **date:** 2026-10-20
- **problem:** Details not fixed by D-0015.
- **options / evidence:**
  - **TLS roots:**
    - The uv-built 3.11 and 3.12 interpreters use a bundled OpenSSL (`OpenSSL 3.5.9`, `cafile=None`, `capath=/etc/ssl/certs`). With that, `cert_store_stats()['x509_ca']` was 0 inside the sandbox (`test_worker_has_system_ca_roots_when_egress_enabled` FAILED on 3.11/3.12 via uv; it passed on Debian's 3.13).
    - Fix: `browser.tls_context()` falls back to the system bundle (`/etc/ssl/certs/ca-certificates.crt`, …). `/etc/ssl` is bind-mounted read-only only into sandboxes that have egress.
    - After the fix the test passed on 3.13 and 3.11. Verification stays on: `test_tls_certificate_is_verified` gave `tls_verify_failed` for a self-signed fixture.
  - **Proxy shutdown latency:**
    - The first version polled `accept()` every 0.1 s, giving `BENCH browser_fetch n=30 p50_ms=136.731`.
    - Replaced with a wake pipe in every `select()`, giving `p50_ms=40.942` (same box, same session).
  - **Fork-bomb safety:** the test launches the unbounded bomb only after the bounded `fork_count` probe shows the limit works. That keeps the `nproc_limit_removed` mutant from running a real fork bomb on the CI host.
  - **CI timeout:** 25 → 45 min. There are now 54 mutants, each running the full suite (about 17 s locally); the 3.13 job took 7m34s with 42 mutants on cycle-004.
- **selected:** as above.
- **rejected:** a sleep-based proxy shutdown; trusting interpreter-default CA paths.
- **tradeoffs:** `/etc/ssl` (public CA material only) is visible to egress-enabled workers.
- **reversal criteria:** CI 3.13 job > 40 min → parallelise the mutation check.

## D-0018: cycle-006 definition (provenance-tagged memory), recorded before code
- **decision_id:** D-0018
- **date:** 2026-10-20
- **problem:** RM-0007. Fetched pages and other external content need to persist without ever becoming instructions, credentials or a tamper-free injection channel.
- **options:**
  - Store: (m1) SQLite with an FTS5 index; (m2) SQLite with LIKE search only; (m3) flat JSONL files.
  - Tamper evidence: (t1) a per-record unkeyed SHA-256 plus a chain; (t2) a per-record HMAC-SHA256 chain under an operator key, with each write's MAC also anchored in the hash-chained audit log.
  - Handler placement: (h1) a sandboxed worker (cannot reach the DB by design, cycle-004); (h2) a repository-owned host-side handler, i.e. TCB code with no tool or model code executing, marked as a new `TrustedHostHandler` kind and audited at bind.
- **evidence (probes 2026-10-20 02:21 UTC+3):**
  - Debian `python3` 3.13.5: `sqlite3.sqlite_version` is `3.46.1`, and `pragma compile_options` includes `ENABLE_FTS5`. `create virtual table t using fts5(x)` plus MATCH returned `[('hello world',)]`.
  - uv 3.11 and 3.12: SQLite `3.53.1`, `fts5 ok`.
  - On GitHub `setup-python` builds FTS5 is UNKNOWN; the test suite prints `MEMORY_SEARCH_MODE=...` in CI.
  - Search quality is not a security property, so a LIKE fallback is acceptable and recorded, not silent.
- **selected:** m1 with an m2 fallback, t2, h2. Every record carries:
  - `record_id`, `source_id`, `origin_url` (byte-exact; a URL containing a secret shape is *refused*, not altered), `source_ref` (e.g. the fetch operation id);
  - `trust` (`untrusted` by default; the capability path cannot set anything else);
  - `content_sha256`, `created_at`, `expires_at`, `seq`, `prev_mac`, `mac`.

  Writes go through `ControlPlane` as `cap.memory.write` (risk 1, idempotent by operation id), with dedup by content hash among live records. Retrieval goes through `cap.memory.search` (risk 0) and returns items wrapped as `{"type": "untrusted_data", "provenance": {...}, "content": ...}`. Rows whose content hash or MAC fails to verify on read are excluded and audited. Secrets are redacted before hashing and storing. Per-record size, total-size, record-count and TTL budgets apply. `browser.fetch` results are ingested as untrusted records via the control plane.
- **cycle definition:**
  - cycle_id: `cycle-006-memory`
  - objective: a provenance-tagged, tamper-evident, budgeted, redacting memory store behind the control plane, fed by `cap.browser.fetch`, whose contents can never widen what policy allows.
  - capability area: `cap.memory.knowledge`
  - budgets: 1 agent session; tokens UNKNOWN; one branch `cycle-6/memory`, one PR; ≤ ~600 new production lines; stdlib only.
  - success metric (all must hold, locally and in CI):
    1. 100% of tests pass, with no skips.
    2. A poisoned-memory run (stored content containing injected action JSON, executed by a deliberately gullible mock model) gives 0 executions outside the actor's grants, and every proposal the model makes beyond policy is refused.
    3. A memory adversarial set (≥ 12 cases: poisoned content, oversized write, store budget, secret in content, secret in URL, tampered content/MAC/trust/deleted row, duplicate writes, trust escalation via args, expired records, FTS query injection) gives 0 violations.
    4. A browser fetch of the local fixture is retrievable with the exact origin URL and provenance.
    5. Prior adversarial sets stay at 20/0, 18/0, 20/0, 21/0 and 26/0.
    6. Every new mutant is killed.
  - expected_value: medium-high (memory is the prerequisite for research and workflows).
  - estimated_cost: ESTIMATE one agent session; token and dollar cost UNKNOWN.
  - expected_revenue: 0.
  - risk: medium. The memory handler is new host TCB code.
  - confidence: medium-high.
  - abort rule: emit `cycle_aborted` if the metric is unmet within budget.
- **rejected:**
  - m3: no index, no atomic dedup.
  - t1: anyone with DB write can recompute unkeyed hashes.
  - h1: a sandboxed worker cannot reach the DB (by design), and opening a DB channel into workers would widen their reach.
- **tradeoffs:**
  - The MAC key lives in the host process, so a same-UID attacker who can read the key can forge records. The anchors in the audit log raise the bar but are not a remote anchor (cycle-010).
  - Tail truncation of the memory DB is detectable only against the audit anchors.
  - Dedup keeps the first record's provenance.
- **reversal criteria:**
  - FTS5 is missing on CI → LIKE fallback, recorded.
  - The memory handler needs to run model- or tool-supplied code → it must move into a worker instead.

## D-0019: Implementation choices made during cycle-006
- **decision_id:** D-0019
- **date:** 2026-10-20
- **problem:** Details not fixed by D-0018.
- **options / evidence:**
  - **Oversized writes are refused twice.** A 64 KiB+1 write through `cap.memory.write` gives `rejected args_too_large` (the control plane's existing request-size limit, before the handler). A direct `MemoryStore.put` of 64 KiB+1 gives `record_too_large`. The schema's `maxLength` (262144) is a looser outer bound.
  - **Dedup scope.** Dedup applies among *live* records (a partial unique index `WHERE purged = 0`). After expiry the same content may be stored again with fresh provenance. A dedup hit returns the first record's id and provenance, and is still audited (`memory_write deduplicated=true`).
  - **Trust.** The capability handler hard-codes `trust="untrusted"`; only direct operator API calls (`MemoryStore.put(trust="operator")`) can set anything else. A `trust` argument is rejected by the schema (`additionalProperties: false`). `source_id="operator"` is just a label and confers nothing (`_trust_args` case).
  - **FTS5 query handling.** Free text is reduced to at most 10 quoted alphanumeric terms, so FTS5 operators and quotes cannot be injected (`_fts_injection`: 7 hostile queries, all `executed`, table intact).
  - **Tamper reporting.** Rows failing verification are excluded from results and audited as `memory_tamper_detected` with `record_id` and `reason` (`mac_mismatch`, `content_hash_mismatch`, `bad_trust`).
- **selected:** as above.
- **rejected:** silently truncating oversized content (it would change the content hash and meaning without notice).
- **tradeoffs:** a dedup hit hides the second origin URL (it is still in the audit log via the decision's args hash, but not in the record).
- **reversal criteria:** a research cycle needs multi-provenance records → add a `sightings` table keyed by `content_sha256`.

## D-0020: cycle-007 definition (research/evidence engine), recorded before code
- **decision_id:** D-0020
- **date:** 2026-10-20
- **problem:** RM-0008. Research output must be reproducible and cited. Every claim must point to verifiable evidence (a source plus a MAC-verified memory record), and sources must follow the Source Handling rules:
  - exact URL;
  - stable id;
  - candidate / `execution_allowed=false` / `trusted=false` by default;
  - lifecycle changes only with recorded evidence.

  Today nothing enforces any of this.
- **options:**
  - Registry placement: (r1) an in-process library the research loop writes directly; (r2) repository-owned `TrustedHostHandler` capabilities behind `ControlPlane` (policy, idempotency, audit), with the run holding only an actor gateway for writes.
  - Source id: (i1) a normalized URL (lowercase host, strip trailing slash, etc.); (i2) `src:` + SHA-256 of the exact URL bytes, with no normalization.
  - Claim evidence: (e1) a URL only; (e2) a pointer `{source_id, record_id, content_sha256, quote}` re-verified against the MAC-chained memory store on every read.
  - Extraction: (x1) model-first; (x2) Tier 0 deterministic regex extraction first, with the model (MockProvider only) consulted only when Tier 0 finds nothing. Model output is parsed as data and its claims pass the same verifier.
- **evidence:**
  - D-0018 / I-0018: memory records carry the exact `origin_url`, `content_sha256` and a MAC that is verified on read (CI run 39342990292).
  - `cap.browser.fetch` is EXTERNAL risk and needs an HMAC approval per operation (cycle-005).
  - The existing tool registry already uses candidate / `execution_allowed=false` for MCP imports (`tests/test_tool_registry.py:75`).
- **selected:** r2, i2, e2, x2.
  - **Lifecycle:** `candidate→evaluating` (needs a verified memory record whose `origin_url` equals the source URL exactly); `evaluating→accepted` (needs a *verified* claim citing the source); `evaluating→rejected|deferred` (needs record or claim evidence); `deferred→evaluating` (needs a record). All other transitions are refused.
  - **Trust:** `accepted` never implies `trusted` or `execution_allowed`. No capability path can set either.
  - **Verifier refuses a claim** when:
    - it has no evidence;
    - a record is missing or tampered;
    - the cited hash differs from the stored one;
    - the record's origin differs from the source URL (URL mutation);
    - the quote is not in the record;
    - the quote does not contain the attribute and value;
    - the subject is not in the record.
  - **Verifier marks a claim UNKNOWN** (it is stored but not used) when:
    - its evidence has expired (stale) or has since become unverifiable;
    - its source was rejected;
    - another verified claim gives the same `(subject, attribute)` a different value (contradicted).
  - **`branch_closed`:** each branch records a comparable metric (name and direction). A discovery pass is "superior" only if a verified, non-contradicted metric claim beats the incumbent. After 3 consecutive non-superior passes the branch is closed, persisted and audited, and further passes are refused before any fetch.
  - **Report:** a canonical JSON artifact plus Markdown, with numbered citations (`source_id`, exact URL, `record_id`, `content_sha256`, quote). It is byte-identical for identical inputs.
- **cycle definition:**
  - cycle_id: `cycle-007-research`
  - objective: a source registry, claim ledger and verifier, plus a deterministic cited research run over local fixtures, all writes through `ControlPlane`.
  - capability area: `cap.research.evidence`
  - budgets: 1 agent session; tokens UNKNOWN; branch `cycle-7/research`, one PR; ≤ ~700 new production lines; stdlib only; local fixture HTTP servers only; MockProvider only.
  - success metric (locally and in CI):
    1. 100% of tests pass, with no skips.
    2. A research adversarial set (≥ 14 cases) gives 0 violations. Cases:
       - fabricated record / hash / quote citations;
       - a mock-model fabricated citation;
       - URL mutation (registry and redirect);
       - source self-promoting trust (content and args);
       - transitions that are illegal or lack evidence;
       - a claim without evidence;
       - contradictory sources;
       - stale evidence;
       - tampered evidence after a claim;
       - a claim from a rejected source;
       - injected actions via the model.
    3. `branch_closed` fires after exactly 3 non-superior passes, and a later pass performs 0 fetches.
    4. The report is byte-identical across two fresh runs, and every claim in it has ≥ 1 resolvable citation.
    5. Prior adversarial sets are unchanged (20/0, 18/0, 20/0, 21/0, 26/0, memory 15/0).
    6. Every new mutant is killed.
  - expected_value: medium-high (gives later cycles cited, auditable inputs).
  - estimated_cost: ESTIMATE one agent session; token and dollar cost UNKNOWN.
  - expected_revenue: 0.
  - risk: medium (new host TCB handlers).
  - confidence: medium-high.
  - abort rule: emit `cycle_aborted` if the metric is unmet within budget.
- **rejected:**
  - r1: research-loop bugs could write trust state without policy or audit.
  - i1: normalization merges distinct URLs and hides URL mutation.
  - e1: a URL alone proves nothing about content.
  - x1: makes the model the source of truth, and it is non-deterministic.
- **tradeoffs:**
  - Fetches still need an operator approval per operation (EXTERNAL risk).
  - The claim ledger rows are not MAC'd. They are re-verified against MAC'd memory, but subject and value binding rely on quote matching.
- **reversal criteria:** any adversarial violation, or report non-determinism.

## D-0021: cycle-007 implementation choices
- **decision_id:** D-0021
- **date:** 2026-10-20
- **problem:** Implement D-0020 within the control plane's existing constraints.
- **options / evidence:**
  - **Capability ids:** `policy.CAPABILITY_ID` is `^cap\.[a-z0-9_]+\.[a-z0-9_]+$`. The first attempt, `cap.research.source.register`, raised `ValueError: invalid capability id`. The ids became `cap.research.source_register`, `cap.research.source_transition` and `cap.research.claim_record` (all `LOCAL_WRITE`), each a `TrustedHostHandler`.
  - **Reading records:** extraction needs a record's exact stored content. `cap.memory.search` is fuzzy, so I added `cap.memory.get` (READ, by `record_id`). It verifies MAC and hash, wraps the result as `untrusted_data`, and audits `memory_tamper_detected` on failure.
  - **Evidence checks run in two layers:** the schema (`minItems: 1`), and then the verifier or registry. Mutation tests call the verifier and registry directly, so a mutant in an inner layer is not masked by the schema.
  - **The transition handler checks legality before evidence**, so an illegal edge reports `illegal_transition` instead of an evidence error.
  - **Quotes must contain the attribute and value as whole tokens.** Tier 1 (model) claims are therefore limited to literal text in the record. Paraphrased model claims are refused, which is deliberate: no claim exceeds its evidence.
  - **Redirects:** a redirect stores the record under the final URL. The source registered for the requested URL therefore cannot reach `evaluating` (`evidence_origin_mismatch`) and stays `candidate`. Content claims are never attributed across URLs.
  - **Duplicate content:** memory dedup means a second URL serving identical content gets the first URL's record (origin mismatch), so it also stays `candidate`.
- **selected:** as above.
- **rejected:** a model-paraphrase path (no verifiable binding to the evidence).
- **tradeoffs:**
  - A redirect-only source can never be evaluated. An operator must register the final URL.
  - Research writes take 3 capabilities plus `cap.memory.get`, i.e. more ControlPlane round trips (B-0013).
- **reversal criteria:** a legitimate research workflow is blocked by the redirect or dedup rule (then add an explicit, audited alias transition).

## D-0022: cycle-008 definition (evidence hardening + workflow scheduler), recorded before code
- **decision_id:** D-0022
- **date:** 2026-10-20
- **problem:** RM-0009 and the user's request for cycle-008.
  - The claim ledger is not MAC'd, so claim deletion or edits are undetectable (S-0009 gap 1).
  - Memory and claim stores are not cross-checked against audit anchors (S-0008 gaps 2-3).
  - There is no way to run work on a schedule without the same work running twice, escaping policy, or ignoring the kill switch.
- **options:**
  - Ledger integrity: (l1) per-row unkeyed hash; (l2) an HMAC chain like memory (seq, prev_mac, mac), with each claim's (seq, mac) anchored in the audit log.
  - Cross-check: (c1) manual; (c2) `integrity.crosscheck`, which verifies the audit chain and then requires that every anchored (seq, mac) is present and identical in the store, that every store row is anchored, and that store heads match. It runs in tests and as a CI step.
  - Job execution: (j1) the scheduler calls handlers directly; (j2) every step is a `ControlPlane.execute` as the job's actor, with `operation_id = job:<job_id>:<slot>:<step>`. The CP idempotency ledger then makes a (job, slot) step execute at most once, even across a crash or restart. A crash inside a step leaves it `in_doubt`, never retried.
  - Slot claim: (k1) in memory; (k2) `INSERT OR IGNORE` on `runs(job_id, slot)` primary key plus a lease (`owner`, `lease_until`) in SQLite.
  - Privilege: (p1) jobs as a capability callable by agents; (p2) jobs defined only through the host operator API `Scheduler.define`, with refusals:
    - the actor lacks a grant for a step;
    - a step's risk is above LOCAL_WRITE (no unattended approvals);
    - a step targets `cap.scheduler.*`;
    - a step carries extra keys such as an actor or approval token.

    Job rows are MAC'd (a tampered actor, step or budget quarantines the job), and policy is re-checked at every run by the CP.
  - Mutation wall time: (s1) keep everything on 3.13 (21m37s in run 39351689327 at 94 mutants; about 26 min projected); (s2) shard mutants `i/3` across the 3 matrix jobs (`MUTANT_SHARD`), each job asserting its own shard count. All 3 jobs are required, so the union is complete.
- **evidence:**
  - `executor.py`: `_from_prior` returns REPLAYED for SUCCEEDED, IN_DOUBT for PENDING. A `BaseException` from a handler escapes `_run` (`except Exception`) and leaves the ledger PENDING, which faithfully simulates a crash.
  - `OPERATION_ID` is `^[A-Za-z0-9._:-]{8,128}$`.
  - `LoopGovernor(budget, kill, clock)` checks the kill switch, iterations, wall time and cost before each unit.
- **selected:** l2, c2, j2, k2, p2, s2.
  - **Schedules:** `interval` (every_s, anchor) or 5-field cron (UTC; `*`, lists, ranges, `*/n`).
  - **Missed slots:**
    - `skip` runs only the latest due slot and records the others as `skipped_missed`;
    - `catch_up` runs at most `catch_up_max` of the most recent missed slots, oldest first, and records the rest as `skipped_missed`.
  - **Concurrency:** a global `max_concurrent` counts live leases, and slots beyond it stay unclaimed (deferred, not lost). Per job, an overlapping slot while a live lease exists is recorded as `skipped_overlap`.
  - **Per-run budget:** a run is a `LoopGovernor` with `max_steps`, `max_wall_s` and `max_cost` on an injectable monotonic clock.
  - **Kill switch:** an engaged kill switch halts `tick` (audited); a run killed mid-way ends `killed`.
  - **Backwards clock:** a per-job high-water mark means slots ≤ the last scheduled slot never re-run. A backwards jump larger than tolerance is audited as `clock_backwards`, and no new slot is claimed until wall time passes the high-water mark.
  - **Crash recovery:** expired leases are re-driven with the same operation ids. Finished steps replay, PENDING steps become `in_doubt`, and steps that never began execute once.
  - **Clocks:** both wall and monotonic clocks are injectable.
- **cycle definition:**
  - cycle_id: `cycle-008-scheduler`
  - objective: a tamper-evident claim ledger, an automated audit-anchor cross-check in CI, and a persistent, exactly-once-per-slot, policy-bounded scheduler.
  - capability areas: `cap.research.evidence` (hardening), `cap.workflow.scheduler`
  - budgets: 1 agent session; tokens UNKNOWN; branch `cycle-8/scheduler`, one PR; **production code ≤ 700 new lines** (hardening ≤ 150, scheduler ≤ 550); stdlib only; no network.
  - success metric (locally and in CI):
    1. 100% of tests pass, with no skips, on 3.11, 3.12 and 3.13.
    2. Claim edits, deletions (middle and tail), forged inserts and memory tail truncation are each detected by the chain and/or the audit cross-check. Affected claims report as `UNKNOWN`.
    3. A CI step runs the cross-check on a real research run: clean is OK, and each injected tamper is detected (`CROSSCHECK ...`).
    4. A scheduler adversarial set (≥ 12 cases) gives 0 violations, and no (job, slot, step) handler effect ever happens twice. Cases:
       - duplicate slot (2 instances);
       - restart storm (≥ 50 restarts);
       - self-scheduling or escalation (ungranted, high-risk, scheduler-cap, extra-key, tampered job row, grant revoked);
       - backwards clock;
       - kill switch mid-run;
       - crash mid-step and crash between steps;
       - overlap;
       - concurrency limit;
       - skip and catch-up caps;
       - run budget.
    5. Prior adversarial sets are unchanged.
    6. Every new mutant is killed. The slowest CI job's wall time is below the 21m37s of run 39351689327.
  - expected_value: medium-high (unattended work, bounded).
  - estimated_cost: ESTIMATE one agent session; token and dollar cost UNKNOWN.
  - expected_revenue: 0.
  - risk: medium (new host TCB, plus concurrency).
  - confidence: medium.
  - abort rule: emit `cycle_aborted` if the metric is unmet within budget.
- **rejected:**
  - l1: recomputable by anyone with DB write access.
  - c1: not automated.
  - j1: bypasses policy, idempotency and audit.
  - k1: lost on restart.
  - p1: CP handlers do not see the calling actor, so job ownership could not be bound safely.
  - s1: CI wall time.
- **tradeoffs:**
  - An `in_doubt` step needs an operator; it is never auto-retried.
  - Cron is UTC only, with no DST semantics.
  - Mutation results are spread across Python versions.
- **reversal criteria:** any double execution, escalation, or violation.

## D-0023: cycle-008 implementation choices (incl. mutation sharding)
- **decision_id:** D-0023
- **date:** 2026-10-20
- **problem:** Implement D-0022 within budget and keep CI wall time reasonable.
- **options / evidence:**
  - **Claim ledger chain:**
    - `ClaimLedger(path, key)` now needs a key of at least 32 bytes.
    - Rows carry `seq`, `prev_mac` and `mac`; the MAC is HMAC-SHA256 over every other column.
    - Reads drop rows whose MAC fails; `verify_chain` checks contiguous `seq` and `prev_mac`.
    - The claim handler anchors `{seq, mac}` of each *new* claim in `claim_recorded`.
    - Evidence: `LEDGER_TAMPER detected=6/6`. Edit, middle delete and memory edit are caught by the chain; tail delete, a forged insert made with the key, and memory tail truncation are caught by the audit anchors.
  - **Report:** `ResearchRun.report` now runs `integrity.crosscheck` over the claims and memory stores. Any failure marks every claim `UNKNOWN [store_integrity:<store>]`; `store_integrity_ok` is reported.
  - **CI cross-check:** `scripts/crosscheck_ci.py` drives a real research run against the fixture, then calls `python -m integrity` (the CLI reads keys from files and never from argv) on the clean store and after each of 6 tampers. It prints `CROSSCHECK ...`.
  - **Scheduler module split:**
    - `schedule.py` (interval and 5-field UTC cron; cron gaps over 366 days are counted as `-1` = UNKNOWN);
    - `store.py` (MAC'd jobs, `runs` PK `(job_id, slot)`, leases, a `take_over` compare-and-swap, `gaps`, wall-clock high-water);
    - `engine.py` (`define` and `tick`).
  - **Crash simulation:** a handler raises `BaseException` (`Crash`). It escapes `ControlPlane._run` (`except Exception`) and leaves the ledger PENDING, as a real process death would. Recovery then sees `IN_DOUBT` and never re-executes.
  - **Exactly-once evidence:** the count of `execute_begin` audit records per `operation_id` is at most 1 in every adversarial case (`max_begins_per_op=1`).
  - **Mutation sharding:** the cycle-007 3.13 job took 21m37s for 94 mutants. Local timing for cycle-8 is 24 new mutants in 468 s, which projects to about 27 min for 118 mutants on one job. I chose `MUTANT_SHARD=i/n` (index mod n), one shard per matrix job (3.11→0/3, 3.12→1/3, 3.13→2/3). Each job asserts it ran exactly its shard's count, and all 3 jobs are required, so the union is the full set.
  - **Excluded mutant:** "high_water not advanced" is not in the set. The `runs` primary key and CP idempotency are defense in depth that make it near-equivalent: re-claims fail and no step runs twice. It is recorded as a known equivalent class, not as a kill.
- **selected:** as above.
- **rejected:** a single 3.13 mutation job (wall time); a separate mutation-only job (it would need its own bubblewrap setup and add a 4th runner).
- **tradeoffs:**
  - A mutant's kill is now observed under one Python version only.
  - A shard's wall time depends on which mutants land in it.
- **reversal criteria:** a mutant that survives only on some Python version, or a shard job above 20 min.

## D-0024: cycle-009 definition (observability + cost accounting), recorded before code
- **decision_id:** D-0024
- **date:** 2026-10-20
- **problem:** RM-0010 and the user's request for cycle-009.
  - No governed operation has metered cost. `estimated_cost` and `measured_cost_usd` are UNKNOWN in every cycle so far (`ecosystem_manifest.json#economics`).
  - Spend is bounded only per process (`LoopGovernor`). Nothing caps spend per run, per job or per day across restarts.
  - Operators get no alert for `in_doubt` runs, quarantined jobs or skipped gaps (S-0010 gap 3).
  - No metrics export exists.
- **options:**
  - Ledger: (e1) extra columns on the CP idempotency ledger; (e2) a separate `EconomicsLedger` (SQLite), HMAC-chained like claims (seq, prev_mac, mac), with each row's (seq, mac) anchored in the audit log and covered by `integrity.crosscheck`.
  - Numbers: (n1) floats with 0 as the default; (n2) every number is a typed `Quantity {value, provenance, unit, source}`, where provenance is one of `measured | configured_estimate | unknown`.
    - `unknown` holds exactly when `value is null`. Arithmetic over any unknown yields unknown; nothing is coerced to 0.
    - `actual_*` fields accept only `measured` or `unknown`.
    - USD derived from measured usage × a configured price is `configured_estimate`, never `measured`. There is no billing API.
  - Revenue: (r1) accept caller-supplied `actual_revenue`; (r2) `actual_revenue` is `measured` only via a registered reconciler. None exists in this cycle, so it is always `unknown`, and any attempt to set it is refused and audited.
  - Metering: (m1) handler self-report; (m2) host-side measurement.
    - Wall time via `perf_counter` around the handler, and the tool-call count, measured by the CP.
    - Egress bytes from the proxy's own `egress_closed` events (host-side), via an audit listener.
    - Model calls and tokens via the provider's `last_usage` when present, else tokens `unknown`.
  - Guards: (g1) post-hoc alerts; (g2) pre-execution policy in `ControlPlane.execute`.
    - Before `ledger.begin`, the guard reserves the configured per-call cost estimate in one `BEGIN IMMEDIATE` transaction, after checking cumulative caps: per-day per actor, per-day global, per-run and per-job, in both USD and call count.
    - If a USD cap applies and the capability's price is UNKNOWN, the call is denied (`budget:cost_unknown`).
    - Caps are cumulative and persisted, so splitting a call or restarting does not reset them.
  - Dry run: a request field `dry_run: true` runs every check (validation, kill switch, policy, schema, approval, rate, budget) and returns `dry_run` with the cost estimate, without reserving, beginning a ledger entry or running the handler.
  - Status report: (o1) ad-hoc SQL; (o2) `python -m economics status --config <json>` emitting JSON or Markdown, read-only. It reports:
    - kill-switch state;
    - scheduler `in_doubt` runs, quarantined jobs (MAC failure), skipped gaps and live leases;
    - budget burn per day vs caps, plus unknown counts;
    - cross-check results for every store.
  - Export: JSONL of ledger entries and of the status report, with every string passed through `redact`.
- **selected:** e2, n2, r2, m2, g2, dry run, o2, JSONL export.
- **cycle definition:**
  - cycle_id: `cycle-009-economics`
  - objective: persistent, tamper-evident economic telemetry for every governed operation, plus pre-execution spend caps, an operator status report and a redacted metrics export.
  - capability areas: `cap.economics.ledger`, `cap.economics.budget_guard`, `cap.observability.status`
  - budgets: 1 agent session; tokens UNKNOWN; branch `cycle-9/economics`, one PR; **production code ≤ 650 new lines**; stdlib only; no network.
  - success metric (locally and in CI):
    1. 100% of tests pass, with no skips, on 3.11, 3.12 and 3.13.
    2. Every governed operation that reaches execution (executed, failed or timed out) has exactly one MAC'd, audit-anchored economics row, holding all 7 fields and a provenance for each.
    3. An economics adversarial set (≥ 10 cases) gives 0 violations:
       - spend-cap bypass by splitting, by many run ids and by restart;
       - forged `actual_revenue` via args, API and DB edit;
       - UNKNOWN coerced to 0 (sum, cap and report);
       - ledger tamper (edit, delete, forged insert) detected by chain and cross-check;
       - secrets in metrics or export;
       - dry run with no side effects;
       - unknown price under a cap.
    4. The status report lists a seeded `in_doubt` run, a quarantined job, skipped gaps, the kill switch, burn and a failed cross-check, in both JSON and Markdown.
    5. Prior adversarial sets are unchanged.
    6. Every new mutant is killed, with sharding kept. The slowest CI job is ≤ 18m21s + 3 min.
  - expected_value: high (a prerequisite for any revenue cycle).
  - estimated_cost: ESTIMATE one agent session; token and dollar cost UNKNOWN.
  - expected_revenue: 0.
  - risk: medium (new code on the CP hot path).
  - confidence: medium.
  - abort rule: emit `cycle_aborted` if the metric is unmet within budget.
- **rejected:**
  - e1: mixes integrity domains, and the idempotency ledger is not MAC'd.
  - n1: silently fabricates zeros.
  - r1: forgeable.
  - m1: workers are untrusted.
  - g1: spends before denying.
  - o1: not operator-usable.
- **tradeoffs:**
  - Reservation uses the configured estimate, so caps bound estimated spend, not billed spend (still UNKNOWN).
  - A reservation can leak (over-count) when `ledger.begin` loses a race. That errs safe.
- **reversal criteria:** any cap bypass, fabricated number, or unanchored row.

## D-0025: cycle-009 implementation choices and deviations from D-0024
- **decision_id:** D-0025
- **date:** 2026-10-20
- **problem:** Concrete choices made while implementing D-0024, and where the code differs from the definition.
- **options / selected:**
  - **Package:** `src/economics/` with `quantity.py` (typed numbers), `ledger.py` (`EconomicsLedger`, `Caps`, `Pricing`, guard, meter), `status.py` (report, Markdown, JSONL export, CLI).
    - The CLI is `python -m economics status --config cfg.json [--format json|md] [--export-jsonl F]`, not `python -m control_plane status`. That keeps the control plane free of imports from scheduler, memory and research.
  - **Hook points:**
    - `ControlPlane(economics=...)`.
    - New request fields `dry_run` (bool) and `budget_scope` (`{run, job}`, each matching `^[A-Za-z0-9._:-]{1,128}$`).
    - New status `DRY_RUN`.
    - Guard order: after policy, schema, idempotency, approval, rate limiter and governor; immediately before `ledger.begin`.
    - The operation row is written right after `_run` returns.
  - **Dry run (deviation):** dry run evaluates validation, kill switch, policy, handler bound, schema, prior-op replay and approval, then the spend guard **without reserving**. It does **not** evaluate the rate limiter or the governor, because both consume budget when checked. D-0024 listed "rate"; this is narrower.
  - **`actual_cost` is a map of Quantities:**
    - `usd`
    - `wall_s` (host `perf_counter`, measured)
    - `tool_calls` (measured)
    - `egress_bytes` (sum of the proxy's own `egress_closed` events during the operation, through a new `AuditLog.listeners` hook; measured)
    - `model_calls`
    - for model calls, `usage_in` / `usage_out` (from provider `last_usage` when it holds valid non-negative ints, else unknown)

    Keys avoid the word "token" because audit redaction masks keys matching `token`.
  - **USD:**
    - A tool operation's `actual_cost.usd` is `unknown` (no billing source).
    - A model call's `usd` is `configured_estimate` = measured usage × configured token price, or `unknown`.
    - No USD value is ever `measured`.
  - **`risk`** is the capability's policy risk level (0-3, `configured_estimate`, source `policy.capability.risk`). **`expected_value`, `expected_revenue` and `confidence`** come from `Pricing.expected` or are `unknown`.
  - **`actual_revenue`** is always `unknown`.
    - `record_revenue` refuses and audits `revenue_refused`.
    - `_put` raises `RevenueRefused` for any non-unknown revenue.
    - `actual_*` meters cannot be `configured_estimate`, except `usd`.
  - **Guard:**
    - One `BEGIN IMMEDIATE` transaction under the ledger lock. Cumulative SQL `TOTAL()` over reservation rows for the day (per actor and global), the run and the job, in USD and call count.
    - A run cap with no run scope is denied (`run_scope_required`). A job cap applies when a job scope is present; the scheduler always sets both, `{"job": id, "run": "id:slot"}`.
    - The runner sets `{"run": run_id}` and checks model calls through the same guard (`model:<provider>` item) **before** calling the provider.
  - **Guard integrity:**
    - When `PRAGMA data_version` shows another connection changed the file, the guard re-verifies the whole chain plus this process's own head (seq, mac).
    - Any failure denies with `budget:ledger_integrity`, so the guard fails closed.
    - The first attempt checked MACs per row on every admit. It cost O(day rows) and missed edits to operation rows, which the adversarial set caught. It was replaced before commit (B-0017).
  - **Redaction:** operation ids and scope ids are redacted before they are stored, because ids can be shaped like secrets (`op-ghp_...`). Bodies are redacted, and export re-redacts every record.
  - **Burn:** reservations without an outcome (a crash, or `in_doubt`) count as `unknown` actual USD, never 0.
  - **Mutants:** 28 new (146 defined), sharded as before.
- **evidence:** `tests/test_economics.py` (15 tests); `tests/test_economics_adversarial.py` (`ECON_ADVERSARIAL cases=16 violations=0`); `MUTANT_ONLY='^econ_'` gives 28/28 killed locally (112 s); B-0017.
- **rejected:** see R-0016.
- **tradeoffs:**
  - Caps bound **configured estimates**, not billed spend.
  - An unpriced capability is denied under any USD cap. That fails closed, but every capability needs a price before USD caps are turned on.
- **reversal criteria:** a cap is exceeded, a number is fabricated, or a tampered ledger is accepted.

## D-0026: cycle-010 definition (ZAIPOS governed lead + outreach workflow), recorded before work
- **decision_id:** D-0026
- **date:** 2026-10-20
- **problem:**
  - The user selected a ZAIPOS lead and outreach workflow for cycle-010, with the user's approval on every send.
  - Prior outreach happened outside the repo (`/workspace/income`, `/workspace/omega`), so nothing governed it:
    - no dedupe across sources;
    - no evidence-backed scoring;
    - no machine-enforced approval, caps, suppression or idempotency;
    - no outcome tracking.
  - RM-0011 forbids live money; outreach is risk-3 communication, not money movement.
- **inputs (read-only, untrusted data):**
  - `/workspace/income/pipeline.csv` (sha256 `a083b511…`, 21 rows);
  - `/workspace/omega/bh_leads_all.csv` (`e5ea2c4c…`, 79 rows);
  - `/workspace/omega/bh_leads_top.csv` (`6ec238f4…`, 30 rows, a subset);
  - `revops_report.md` (`b76bc225…`): the operating limits (≤ 4 sends/day, honour "no thanks", stop after 2 touches) and the offer facts (free 30-day pilot, then BHD 12/branch/month);
  - pitch files (`email_pitches_*`, `cafe_pitches.md`), used only as evidence of prior contact, never as template text that is executed.
- **options:**
  - Fixtures: (f1) commit the real lead CSVs to the repo; (f2) copy the real inputs to `/workspace/outreach_fixtures/` outside git with a sha256 manifest, run the pipeline on them from there, and commit only synthetic fixtures (including injection cases) for tests and CI.
  - Drafting: (t1) a model writes drafts; (t2) repo-owned templates, where every personalization slot is filled only from a named source field, the slot→field→source mapping is stored with the draft, and a clause is omitted when its field is missing. No model is used.
  - Send authorization: (a1) a queue flag; (a2) `cap.outreach.send` registered at risk 3 (IRREVERSIBLE), so the ControlPlane requires an HMAC approval token bound to actor, capability, operation id and args. The args are exactly `{draft_id, draft_hash, recipient, channel}`, and `draft_hash` = sha256 over the canonical (channel, recipient, subject, body). At send time the handler re-reads the MAC'd draft and recomputes the hash, so editing a draft after approval is refused.
  - Transport: (x1) wire a connector; (x2) `DenyTransport` by default plus `dry_run=True` by default; only `MockTransport` exists in code. No connector is wired.
- **selected:** f2, t2, a2, x2.
  - **Queue:** SQLite, MAC over draft content. Every draft is inserted as `pending_approval`. States: `pending_approval → sent | suppressed | rejected | failed`. `sends.draft_id` is UNIQUE, plus CP idempotency on `operation_id = outreach:<draft_id>`, so no double send.
  - **Caps:** per recipient (lifetime touches ≤ 2, ≤ 1 per day) and per UTC day (≤ 4). They are enforced in the send handler in one transaction, with recipients normalized (case, whitespace, `+tag`, trailing dot) so splitting cannot evade them. Economics `Caps(day_calls)` are an optional second layer.
  - **Suppression/opt-out:** from pipeline statuses `BOUNCED` and `CLOSED_NO_REPLY` (2 touches), and from tracker opt-outs. Checked at draft time and again at send time.
  - **Economics:** the CP records the economics row of every send attempt that executes (cycle-009).
  - **Tracker:** reply state and outcome default to UNKNOWN. Revenue stays UNKNOWN and refuses numbers until verified (no reconciler; RM-0011).
  - **CLI:** `python -m outreach build|export` writes pending drafts as JSONL plus Markdown for review.
- **cycle definition:**
  - cycle_id: `cycle-010-outreach`
  - objective: a governed, evidence-backed ZAIPOS lead → draft → approval-gated outbound pipeline that cannot send without a human token, with a mock transport only.
  - capability areas: `cap.outreach.leads`, `cap.outreach.drafts`, `cap.outreach.send` (risk 3), `cap.outreach.tracker`
  - budgets: 1 agent session; tokens UNKNOWN; branch `cycle-10/outreach`, one PR; **production code ≤ 700 new lines**; stdlib only; no network; **0 real sends**.
  - success metric (locally and in CI):
    1. 100% of tests pass on 3.11, 3.12 and 3.13.
    2. Every generated draft is `pending_approval`. Every personalized slot maps to a source field, and draft text holds no lead fact absent from the source.
    3. An outreach adversarial set (≥ 10 cases) gives 0 violations:
       - send without approval;
       - token reused for another draft, recipient or channel;
       - draft tampered after approval;
       - suppressed or opted-out recipient;
       - cap evasion by splitting (multiple drafts, recipient case/+tag variants, day boundary);
       - prompt injection in lead fields;
       - double send / replay;
       - transport default deny and dry run;
       - revenue forged in the tracker.
    4. The real-data run produces N drafts, reported as counts only, with 0 transport calls.
    5. Prior adversarial sets, the cross-check and the status CI are unchanged.
    6. Every new mutant is killed in the sharded mutation check.
  - expected_value: medium (unblocks approved ZAIPOS outreach at ≤ 4/day).
  - estimated_cost: ESTIMATE one agent session; dollar cost UNKNOWN.
  - expected_revenue: UNKNOWN (revops target ≥ BHD 12 by 2026-11-30 is a goal, not evidence).
  - risk: medium (outbound communication; mitigated by risk-3 approval and the mock transport).
  - confidence: medium.
  - abort rule: emit `cycle_aborted` if the metric is unmet within budget.
- **rejected:**
  - f1: puts third-party contact data in git history.
  - t1: can invent facts about a lead.
  - a1: bypassable.
  - x1: the user forbade sending.
- **reversal criteria:** any send without a valid token, any invented lead fact, or any cap or suppression bypass.

## D-0027: cycle-010 implementation choices and real-data run
- **decision_id:** D-0027
- **date:** 2026-10-20
- **problem:** Concrete choices made while implementing D-0026, and the evidence from the real-data run.
- **selected:**
  - **Package:** `src/outreach/` with `leads.py` (ingest, clean, dedupe, rules), `drafts.py` (templates), `store.py` (outbox, suppression, sends, outcomes), `handlers.py` (capabilities, transports, caps), `pipeline.py` (runtime, build, export, CLI).
  - **Ingest:**
    - Only whitelisted columns are read. Control, bidi and zero-width characters are replaced and length is capped.
    - The recipient keeps its original spelling when valid. `norm_email` (lowercase, `+tag` stripped, trailing dot) is used only for dedupe keys, caps and suppression.
    - Dedupe is union-find over email, phone digits (≥ 7) and normalized name. A field conflict keeps the first source and is recorded with both provenances.
    - Pipeline `status` and `touches` always come from `pipeline.csv`.
  - **Hold rule:** the slot fields `name` and `area` must be letters or digits (any script) plus `&'’.,-()°/#+!`, and must not match the instruction/markup/URL pattern. Otherwise the lead is held and never drafted. `category` enters text only through a fixed phrase map.
  - **Scoring:** 7 rules (email +3, phone +1, food service +2, multi-branch +2, live website +1, social-only +1, hours +1; max 11). The source CSV's `fit_score` is ignored as untrusted. Each rule hit records field, value and provenance.
  - **Templates:** `zaipos_email_v1` with an opt-out line ("reply stop"). Offer terms come from `revops_report.md` (free setup, 30 days, BHD 12/branch/month). The signature is the name only.
  - **Send args:** `{draft_id, draft_hash, recipient, channel, mode}`.
    - `mode` is in the signed args, so a dry-run token cannot authorize a live send.
    - The operation id is `outreach:<mode>:<draft>:<attempt>`. A new attempt never re-sends, because the `sends` PRIMARY KEY and the `pending_approval` state check enforce at-most-once.
    - `OutreachContext.dry_run=True` forces dry even for `mode=live` and reports `forced`.
  - **Runtime:** `open_runtime` wires DenyTransport and dry-run. No real transport class exists, and no connector is wired.
    - The ControlPlane is built with `require_host_hardening=False` for CLI use (deviation; S-0012 gap 6).
    - Keys are generated 0600 in the state dir.
  - **Real data:**
    - Copies are in `/workspace/outreach_fixtures/` (outside git, `MANIFEST.sha256`; the originals still verify byte-identical after the run).
    - The state is in `/workspace/outreach_state/` (outside git).
- **evidence (counts only, no PII):**
  - Real build: `records=130 leads=94 merged=36 suppressed=7 prior_pending=10 no_email=68 held=0 drafts_created=9 pending_approval=9`.
  - Re-run: `drafts_existing=9 drafts_created=0` (idempotent).
  - Export: `exported_pending=9`.
  - Outbox: `pending_approval=9`, `sends=0`. Suppression rows: `bounced=1`, `closed_no_reply_after_2_touches=4` (2 suppressed leads had no email).
  - Audit: `execute_begin=9` (all `cap.outreach.enqueue`), `outreach_sent=0`, `outreach_dry_run=0`.
  - `REAL_DRAFTS reproducible_from_source_slots 9 violations 0`.
- **rejected:** see R-0017.
- **tradeoffs:**
  - Most real leads (68/94) have no public email and get no draft. WhatsApp and walk-in channels are not modelled.
  - Name-key dedupe can merge two different businesses that share a name.
- **reversal criteria:** any invented fact, or any send path without a token.

## D-0028: cycle-011 definition (Gmail two-phase handoff transport), recorded before work
- **decision_id:** D-0028
- **date:** 2026-10-20
- **problem:**
  - PR #10 merged (`a887a7bc0a6cec0b40cda470d69328c45e2fe476`). The user chose to connect the outreach pipeline to their Gmail (zanabalmuhamed@gmail.com) and send the 9 pending ZAIPOS drafts through the caps.
  - The repo's Python code cannot call the Gmail connector (the connector lives in the agent runtime, not in this process), so a direct `transport.send` is impossible.
  - The user granted standing permission on 2026-10-20 to send ZAIPOS drafts once the pipeline is merged. S-0012 gap 1: the approval key sits in the shared state folder.
- **options:**
  - (h1) Give Python a Gmail credential and an HTTP client. (h2) A two-phase handoff: Python *reserves* sends through `cap.outreach.send` and emits a manifest; the external sender (the agent with the Gmail connector) sends exactly the manifest entries, then Python *records* the result through a governed confirm capability.
  - Approval: (p1) one human-typed token per draft; (p2) a standing-permission grant recorded in the outbox and audit log, from which an issuer mints one token per draft bound to draft hash, recipient, channel, mode and grant reference.
- **selected:** h2, p2.
  - `python -m outreach send-plan`: for each `pending_approval` draft, the issuer mints a token and calls `cap.outreach.send` with `mode=handoff`. The handler re-checks MAC, hash, opt-out line, suppression and caps (4/day, 1/recipient/day, 2/recipient total; reservations count) inside one transaction, and inserts a reservation instead of calling a transport. Output: a manifest (reservation id, recipient, subject, body file, body sha256, expiry) plus read-only body files.
  - `record-sent --reservation R --message-id M` / `record-failed`: through `cap.outreach.confirm` (LOCAL_WRITE). Confirming twice is an idempotent replay or a conflict, never a second send row. An unknown reservation is refused.
  - Reservations not confirmed before expiry become `in_doubt`. They keep counting against caps and are never re-planned.
  - `record-reply`: through `cap.outreach.reply`. Opt-out keywords (stop, unsubscribe, not interested, remove me, opt out) suppress the sender and set the outcome; open reservations to that recipient are cancelled.
  - Every body must contain the opt-out line; enqueue and send refuse otherwise.
  - The approval key moves out of the state dir (explicit path, env var, or `~/.config/grok-bot/`); a key inside the state dir or readable by group/other is refused.
- **cycle definition:**
  - cycle_id: `cycle-011-gmail-transport`
  - objective: a governed two-phase handoff transport so the user can send approved ZAIPOS drafts via Gmail within caps, with every send planned, token-bound and recorded.
  - capability areas: `cap.outreach.send` (risk 3, new mode `handoff`), `cap.outreach.confirm` (new), `cap.outreach.reply` (new), standing approval issuer.
  - budgets: 1 agent session; tokens UNKNOWN; branch `cycle-011-gmail-transport`, one PR; **production code ≤ 600 new lines**; stdlib only; no network; **0 real sends; no Gmail send tool called**.
  - success metric (locally and in CI):
    1. 100% of tests pass on 3.11, 3.12 and 3.13.
    2. A handoff adversarial set (≥ 8 cases) gives 0 violations: confirming a reservation that is not in the manifest / unknown; confirming the same reservation twice; cap evasion (re-plan, variants, failed-then-replan, in-doubt); suppressed recipient after opt-out; body edited after planning (file and DB); token without a recorded grant or for a revoked/exhausted grant; key custody (key inside state dir or world-readable refused).
    3. The cycle-010 outreach adversarial set stays 15/0; prior sets, cross-check and status CI unchanged.
    4. Every new mutant is killed in the sharded mutation check.
    5. A synthetic end-to-end CLI run (grant → send-plan → verify-manifest → record-sent/failed → record-reply) is in CI.
  - expected_value: high (unblocks the user's approved sends).
  - estimated_cost: ESTIMATE one agent session; dollar cost UNKNOWN.
  - expected_revenue: UNKNOWN.
  - risk: medium-high (real outbound email after merge); mitigated by token binding, caps, manifest verification and the human/agent doing the actual send.
  - confidence: medium.
  - abort rule: emit `cycle_aborted` if the metric is unmet within budget.
- **rejected:** h1 (puts a mail credential in the repo runtime and gives Python a send path the user did not ask for); p1 (the user granted standing permission; per-draft binding is kept by minting per-draft tokens).
- **reversal criteria:** any send recorded without a reservation, any reservation beyond caps, any re-plan of an in-doubt or sent draft, or any send to a suppressed recipient that the tools did not flag.

## D-0029: cycle-011 implementation choices and deviations from D-0028
- **decision_id:** D-0029
- **date:** 2026-10-20
- **problem:** Concrete choices made while implementing D-0028.
- **selected:**
  - **New modules:** `src/outreach/issuer.py` (standing grants and per-draft token minting) and `src/outreach/handoff.py` (send-plan, verify-manifest, record, record-reply, status). Extended: `handlers.py` (mode `handoff`, `HandoffTransport`, `cap.outreach.confirm`, `cap.outreach.reply`, `classify_reply`), `store.py` (`reservations`, `grants`, cap counting, sweep, cancel-on-suppress), `pipeline.py` (key custody and CLI), `drafts.py` (`OPT_OUT_LINE` constant; body bytes unchanged).
  - **Reservation:** inside the same `BEGIN IMMEDIATE` transaction as the cap check. `reservations.draft_id` is UNIQUE, so a draft is planned at most once ever. The id is `rsv:` + 96 random bits, so a reservation from another outbox (or a guessed one) is unknown here. TTL 12 h, then `in_doubt`.
  - **Caps** count `sends` plus reservations in `reserved` or `in_doubt`. `failed` and `cancelled` reservations release the cap, but their drafts are terminal (never re-planned).
  - **Operation id** `outreach:handoff:<draft>:<UTC day number>`: a cap refusal today replays today and is retried tomorrow under a new id.
  - **Manifest** lists every open (`reserved`) reservation, so re-running `send-plan` never drops an unsent entry. Fields: `reservation_id, recipient, subject, body_file, channel, draft_id, draft_hash, body_sha256, expires_at`. Body files are 0400, the manifest 0600.
  - **verify-manifest** statuses: `ok`, `not_reserved`, `state_<state>`, `expired`, `draft_changed`, `suppressed`, `manifest_edited`, `body_file_changed` (comma-joined). Exit 0 only if every entry is ok.
  - **Confirmation** uses operation id `confirm:<rid>`: the same result again is `replayed`; a different message id or result is a `conflict`; a fresh op id is refused by the handler (`already_sent|failed`). A message id cannot back two reservations. A send that did happen is recorded even when anomalous (`late_in_doubt`, `after_cancel`, `body_file_changed`, `draft_changed`), flagged in the result and audit, and the CLI exits 3.
  - **Replies:** the text is classified only (the CP ledger stores its hash, not the text). Lines starting with `>` and everything after a quote header (`On … wrote:`, `-----Original Message-----`, `From:`) are ignored, because our own body says `reply "stop"`. Keywords: stop, unsubscribe, not interested, remove me, opt out/opt-out, do not contact/email. An opt-out suppresses the sender (even an unknown one), sets lead outcomes to `opted_out` and cancels open reservations.
  - **Approval key:** `--approval-key`, `$OUTREACH_APPROVAL_KEY`, or `~/.config/grok-bot/outreach_approval.key` (created 0600 in a 0700 dir). Refused if it is inside the state dir or has group/other permission bits. `send-plan` reports `legacy_approval_key_in_state` when the cycle-010 key still exists (it is no longer read).
  - **Grant rows** are MAC'd with the approval key. Tokens: TTL 600 s, approver `standing:<ref>`, `outreach_approval_issued` audited per draft. A grant covers one template and a `max_sends` ceiling (≤ 50) counted over reservations.
- **deviations from D-0028:** none in scope. Production code: 553 new lines (budget 600).
- **evidence:** local `Ran 224 tests OK`; `HANDOFF_ADVERSARIAL cases=9 violations=0`; `OUTREACH_ADVERSARIAL cases=15 violations=0`; `HANDOFF_CLI reserved=4 verified_ok=4 recorded_sent=1 opt_out=1 transport_calls=0`.
  - **Rehearsal on a throw-away copy of the real state** (deleted afterwards; the real state was not opened, its mtimes still 10:39 UTC+3): `pending_approval=9` → `send-plan` `reserved=4`, `skipped={cap:day: 5}`, `verify ok 4/4`, opt-out line in 4/4 bodies, `legacy_approval_key_in_state=true`, sends 0. Counts only.
- **tradeoffs:** a failed send is terminal for its draft (no automatic retry); a retry needs a new draft. The cap day is the UTC day (UTC+3 local: the day rolls over at 03:00 local).
- **reversal criteria:** any metric regression, or any real send recorded without a reservation.
