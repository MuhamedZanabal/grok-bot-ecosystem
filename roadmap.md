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
