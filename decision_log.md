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
