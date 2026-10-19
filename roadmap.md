# Roadmap (append-only)

## RM-0001: Cycle order after Cycle 1
- **decision_id:** RM-0001
- **date:** 2026-10-19
- **problem:** Sequence the next cycles.
- **options:** default order, or reorder for revenue.
- **evidence:** no cost or revenue telemetry exists (`ecosystem_manifest.json#economics`), so reordering has no evidence (see D-0001).
- **selected:** default order:
  - **Cycle 2:** model-neutral agent orchestration substrate. Agents only *propose* `execute()` requests and never hold handlers. Gap carried in: persistent, versioned grants (`cap.control.persistent_policy_store`).
  - **Cycle 3:** typed tool + MCP execution substrate behind `ControlPlane.execute`, with schema-validated args and process isolation for handlers.
  - **Cycle 4:** browser/computer-use substrate.
  - **Cycle 5:** memory/knowledge substrate.
  - **Cycle 6:** research + evidence engine.
  - **Cycle 7:** workflow/scheduling engine (approval expiry and retry semantics).
  - **Cycle 8:** observability + cost accounting (real token/$ metering; feeds `governor.admit(cost)`).
  - **Cycle 9:** security hardening (key custody/rotation, audit anchoring, sandboxing, pinned dev tooling).
  - **Cycle 10+:** autonomous revenue generation, only after Cycles 8-9 controls and economic telemetry are evidenced.
- **rejected:** a revenue sub-cycle now, with no telemetry to evidence it.
- **tradeoffs:** revenue is deferred until the cost and risk controls exist.
- **reversal criteria:** a revenue workflow can run entirely at risk levels 0-1 with measured cost; then it may be proposed earlier, with evidence recorded in decision_log.md.

## RM-0002: Roadmap after Cycle 2
- **decision_id:** RM-0002
- **date:** 2026-10-19
- **problem:** Carry Cycle 2 gaps into the next cycles.
- **options:** keep the default order, or pull security hardening forward.
- **evidence:** security_log.md S-0003 gaps 1-5 and S-0004.
- **selected:** default order, with explicit scope additions:
  - **Cycle 3 (typed tool + MCP substrate):** must include out-of-process handler execution (`cap.tools.handler_isolation`), closing S-0003 gap 1 and flipping `test_known_gap_in_process_introspection`, plus per-capability JSON-schema validation of args.
  - **Cycle 7:** persisted run state machine (replaces resume-by-rerun, D-0007) and approval-expiry handling for paused runs.
  - **Cycle 8:** real token/$ metering to replace `RunConfig.model_call_cost` estimates.
  - **Cycle 9:** grant-store authentication and file permissions, HMAC key custody, audit anchoring, and tool-output content filtering.
  - Branch protection: blocked on plan (S-0004); needs a user decision.
- **rejected:** pulling Cycle 9 forward. The highest-risk open gap (in-process reach) is structurally fixed by Cycle 3 isolation.
- **tradeoffs:** Cycle 3 grows slightly in scope.
- **reversal criteria:** a real provider adapter is added before Cycle 3, in which case isolation must land first.

## RM-0003: Roadmap after Cycle 3
- **decision_id:** RM-0003
- **date:** 2026-10-19
- **problem:** Carry Cycle 3 gaps forward.
- **options:** keep the default order, or pull Cycle 9 forward.
- **evidence:** S-0005 open gaps 1-7, S-0004.
- **selected:** default order. Cycle 4 (browser/computer-use) must run its driver inside a `SubprocessHandler` worker, and before it handles credentials it must either (a) enforce `set_non_dumpable()` in a control-plane host entry point, or (b) pull forward the Cycle 9 items for uid/namespace isolation and persisted registry state. Live MCP transport stays deferred (R-0006) until a cycle needs it, and then runs inside a worker.
- **rejected:** reordering now. Cycle 4 has no credentials in scope yet.
- **tradeoffs:** filesystem reach by tools remains until Cycle 9.
- **reversal criteria:** any cycle that gives tools access to real credentials or external accounts must first close S-0005 gaps 1-2.

## RM-0004: Renumbered roadmap (security sub-cycle inserted)
- **decision_id:** RM-0004
- **date:** 2026-10-20
- **problem:** Insert the isolation sub-cycle per D-0012 and renumber honestly.
- **options:** keep the numbers and use a "3b" label, or renumber.
- **evidence:** D-0012.
- **selected:** renumber. Done: cycle-001 control plane, cycle-002 orchestration, cycle-003 tool substrate. Then:
  - **cycle-004-isolation (this):** OS sandbox for workers, enforced host hardening, persisted registry trust, descendant kill.
  - **cycle-005:** browser/computer-use substrate (formerly Cycle 4).
  - **cycle-006:** memory/knowledge (formerly 5).
  - **cycle-007:** research + evidence engine (formerly 6).
  - **cycle-008:** workflow/scheduling (formerly 7).
  - **cycle-009:** observability + cost accounting (formerly 8).
  - **cycle-010:** remaining security hardening: key custody/rotation, audit anchoring, tool-output content filtering, grant-store auth (formerly 9, minus what cycle-004 pulled forward).
  - **cycle-011+:** autonomous revenue generation (formerly 10+), gated as before.
- **rejected:** a "3b" label, which hides the reorder.
- **tradeoffs:** older log entries refer to the old numbers ("Cycle 7", "Cycle 8", "Cycle 9"). They are append-only and left as written. This entry is the mapping.
- **reversal criteria:** none.

## RM-0005: After cycle-004-isolation
- **decision_id:** RM-0005
- **date:** 2026-10-20
- **problem:** Choose the next target.
- **options:** cycle-005 browser/computer-use (as RM-0004), or more hardening first.
- **evidence:** S-0006 closed RM-0003's precondition (S-0005 gaps 1-2). Remaining gaps 1, 4 and 7 are relevant to credential handling.
- **selected:** cycle-005 browser/computer-use. Its driver runs inside the sandbox; any network access it needs must be an explicit, narrow, audited egress design, never a default. The cycle must add `RLIMIT_NPROC` or a cgroup pids limit before it handles credentials, and must not store credentials in any SQLite store reachable by same-UID host processes without cycle-010 signing (otherwise it records an UNKNOWN or gap).
- **rejected:** further hardening first, which would push value further out. Gaps are tracked.
- **tradeoffs:** seccomp is still absent.
- **reversal criteria:** the CI or production probe shows bwrap blocked on target hosts → do D-0013's reversal path first.
