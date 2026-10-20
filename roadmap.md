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

## RM-0006: cycle-005 scope and credential-broker design (not implemented)
- **decision_id:** RM-0006
- **date:** 2026-10-20
- **problem:** Record cycle-005 and the credential design so later cycles cannot leak secrets to models.
- **options:** n/a.
- **evidence:** D-0015, D-0016.
- **selected:**
  - **cycle-005-browser-egress (this cycle):** egress proxy, RLIMIT_NPROC, `cap.browser.fetch`.
  - **Credential broker interface (design only):**
    - `CredentialBroker.bind(capability_id, credential_ref, scope)` is operator-only and audited. It stores a *reference* (vault path or keyring id); secret bytes are never in SQLite, the audit log or a WorkerSpec.
    - Injection happens in the **egress proxy, host side**. For an allowlisted `host:port` with a bound credential, the proxy terminates TLS for that destination only, adds the header or cookie to the outgoing request, and strips any `Set-Cookie` or auth echo from the response before bytes reach the worker.
    - The worker, and therefore the tool and the model, only ever see a `credential_ref` handle string, never the secret.
    - Every injected use is audited with a credential_ref hash and the destination, never the value.
    - Binding requires capability risk ≥ EXTERNAL and an approval token.
    - Rotation, revocation and expiry belong to the cycle-010 key custody work.
  - **Open design questions (UNKNOWN):** host-side TLS interception needs a per-destination CA trusted by the worker only; how session state for multi-step logins is held without reaching the model.
- **rejected:** implementing it now (out of scope by instruction).
- **tradeoffs:** none.
- **reversal criteria:** n/a.

## RM-0007: After cycle-005
- **decision_id:** RM-0007
- **date:** 2026-10-20
- **problem:** Next target.
- **options:** cycle-006 memory/knowledge (per RM-0004), or a JS-capable browser tier.
- **evidence:** S-0007 gaps 1-2 (exfiltration to allowlisted hosts; untrusted page content) matter most once memory persists page content.
- **selected:** cycle-006 memory/knowledge, storing fetched content as *untrusted, provenance-tagged* records (source URL, fetch audit seq, hash) and never as instructions. The JS tier (CDP to system Chrome via an in-sandbox forwarder to the same proxy) is a later sub-cycle, triggered by the first capability that needs it (D-0015 reversal criteria).
- **rejected:** the JS tier now (no in-scope need).
- **tradeoffs:** none.
- **reversal criteria:** a capability needs JS rendering.

## RM-0008: After cycle-006
- **decision_id:** RM-0008
- **date:** 2026-10-20
- **problem:** Next target.
- **options:** cycle-007 research and evidence engine (per RM-0004).
- **evidence:** memory now holds provenance-tagged untrusted records (S-0008); research needs claims that cite record ids and origin URLs.
- **selected:** cycle-007 research and evidence engine. Claims must cite `record_id` and `content_sha256`, and a claim without a verifiable citation is UNKNOWN. Also add an automated audit-anchor cross-check for memory (S-0008 gaps 2-3) and per-actor memory namespaces if more than one actor is granted.
- **rejected:** none.
- **tradeoffs:** none.
- **reversal criteria:** none.

## RM-0009: After cycle-007
- **decision_id:** RM-0009
- **date:** 2026-10-20
- **problem:** Next target.
- **options:**
  - (a) cycle-008 workflow scheduler (RM-0004 order);
  - (b) hardening the evidence substrate (MAC-chained claim ledger, automated memory audit-anchor cross-check, per-actor namespaces);
  - (c) cost observability.
- **evidence:** S-0009 gaps 1 and 6 and S-0008 gaps 2-3 are integrity gaps in the substrate that research depends on. The scheduler would multiply unattended runs over that substrate.
- **selected:** cycle-008 = (b) first, scoped to the claim-ledger chain plus the audit-anchor cross-check; then the scheduler.
- **rejected:** scheduler first (it would automate over an unverifiable ledger).
- **tradeoffs:** delays workflows by a cycle.
- **reversal criteria:** the user prioritizes workflows or revenue.

## RM-0010: After cycle-008
- **decision_id:** RM-0010
- **date:** 2026-10-20
- **problem:** Next target.
- **options:**
  - (a) cost and observability (metering, per-job/per-run spend, alerts for `in_doubt` and quarantine);
  - (b) an external audit witness (anchoring the audit head outside the box);
  - (c) revenue workflows.
- **evidence:** cycle-008 introduced unattended runs whose spend is unmetered (`estimated_cost` is UNKNOWN in every cycle so far), and `in_doubt` / quarantine states that nobody is alerted to (S-0010 gap 3).
- **selected:** cycle-009 = (a), cost and observability, scoped to per-run metering through the governor plus an operator status report (`in_doubt`, quarantined, skipped gaps).
- **rejected:** (c) before metering.
- **tradeoffs:** (b) deferred.
- **reversal criteria:** the user prioritizes revenue or external anchoring.

## RM-0011: After cycle-009: live-money prerequisites, stated precisely
- **decision_id:** RM-0011
- **date:** 2026-10-20
- **problem:**
  - Which revenue-cycle prerequisites does cycle-009 meet?
  - What remains before any workflow that can move real money?
- **evidence:** D-0024, D-0025, S-0011, and the CI result of PR #9 (I-0025).
- **prerequisite status:**

  | prerequisite | status | evidence / what is missing |
  |---|---|---|
  | economic telemetry (per-op expected/estimated/actual cost and revenue, risk, confidence, provenance) | **MET** (cycle-009, CI-pending until I-0025) | `EconomicsLedger`; every executed/failed/timed-out op has one MAC'd, anchored row; UNKNOWN stays explicit |
  | spend caps before execution (per run/job/day, dry run) | **MET for configured estimates** | `Caps`; S-0011 gap 1: billed spend is not measured |
  | billed-cost measurement | **NOT MET** | no provider billing or invoice source; USD is `configured_estimate` or `unknown` |
  | balance guards (refuse if the balance source is below a floor or unreadable) | **NOT MET** | no balance source; no balance-read capability |
  | exposure / drawdown limits | **NOT MET** | no position or exposure model; spend caps are not exposure limits |
  | venue allowlist | **NOT MET** (network-level only) | egress allowlists exist per capability (cycle-005); there is no venue, counterparty or market allowlist in policy |
  | emergency stop | **PARTIAL** | the kill switch halts the CP and scheduler (tested); there is no venue-side cancel/flatten, and it is untested against any venue |
  | reconciliation (ledger vs venue statements) | **NOT MET** | no reconciler; `actual_revenue` is always UNKNOWN and `record_revenue` refuses |
  | external audit witness | **NOT MET** | S-0010 gap 1 and S-0011 gap 5 |
  | human approval for irreversible ops | **MET** (since cycle-001) | HMAC approval tokens for risk ≥ 2, bound to op, args and expiry |

- **conclusion:** no live-money workflow may be proposed. Revenue cycles stay limited to risk 0-1 workflows with no money movement, with revenue UNKNOWN until a reconciler exists.
- **options for cycle-010:**
  - (a) a read-only balance source plus a reconciler against a **paper/mock venue only**, with venue allowlist and exposure/drawdown policy objects (the first 4 NOT MET rows, still no real money);
  - (b) an external audit witness;
  - (c) push alerting for the status report.
- **selected:** recommend (a), paper venue only, then (b). The user decides.
- **rejected:** any real-venue or real-wallet integration before (a) and (b) are evidenced. Phantom/wallet MCP tools available to the operator are explicitly out of scope for repo code.
- **reversal criteria:** the user directs otherwise, with the risk recorded.
