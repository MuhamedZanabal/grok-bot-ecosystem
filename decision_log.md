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
