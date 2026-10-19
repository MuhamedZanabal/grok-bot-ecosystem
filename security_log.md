# Security log (append-only)

## S-0001: Cycle 1 controls and adversarial verification
- **decision_id:** S-0001
- **date:** 2026-10-19
- **problem:** Baseline had no security controls. Establish deny-by-default enforcement with evidence.
- **options:** trust callers to check policy, or enforce in one gate with tests that attack it.
- **evidence:**
  - `tests/test_adversarial.py`: 20 attack cases (no grant, unknown capability, ID injection, extra privilege field, L3 without approval, amount/payee tampering, smuggled arg, token reuse across op/actor/capability, forged signature, payload swap, rogue key, expired token, kill switch, NaN, oversize, path-like op id) -> `ADVERSARIAL cases=20 unauthorized_executions=0`.
  - `bash scripts/mutation_check.sh` -> `MUTATION mutants=10 killed=10`. Disabling any one of policy, kill switch, approval, replay, args-hash, expiry, scope, cost budget, redaction or validation fails the suite. The first run found `skip_replay` SURVIVED; `test_replay_consumes_no_budget_and_needs_no_new_approval` was added to kill it.
  - Redaction: `tests/test_audit.py::test_secrets_never_reach_disk` (GitHub/OpenAI/Slack/AWS-style tokens, Bearer headers, PEM keys, sensitive key names, approval tokens). Handler exception messages are never logged, only the exception type (`tests/test_failures.py::test_handler_exception_is_failed_and_terminal`).
  - Tamper evidence: hash-chained audit; `tests/test_audit.py::test_chain_verifies_and_detects_tampering`, `test_deleted_record_detected`.
- **selected:** single-gate enforcement plus adversarial and mutation tests in CI.
- **rejected:** caller-side checks.
- **tradeoffs / known gaps (open):**
  1. HMAC key custody is the caller's job; there is no rotation or KMS (Cycle 9).
  2. The audit log is tamper-evident, not tamper-proof. An attacker with write access can rewrite the entire chain. External anchoring is a gap.
  3. A timed-out handler thread cannot be killed in Python. It is marked `timed_out` but may still be running. Process isolation is a gap (Cycle 3/9).
  4. Grants are in memory and not persisted or versioned.
  5. The control plane is in-process, so code in the same process can bypass it. Enforcement is real only when agents cannot reach handlers directly (Cycle 2/3).
- **reversal criteria:** any adversarial or mutation case regressing blocks merge.

## S-0002: Fixed trailing-newline bypass in ID validation
- **decision_id:** S-0002
- **date:** 2026-10-19
- **problem:** During development, `re.match(r"^...$")` accepted `"cap.fs.read\n"` because `$` matches before a trailing newline. The adversarial case reached policy and was only stopped there as an unknown capability.
- **options:** strip input, or use `fullmatch`.
- **evidence:** all ID checks now use `.fullmatch(` (`grep -rn "match(" src/`). The adversarial case now returns `rejected / bad_capability`. Regression test in `tests/test_policy.py::test_cannot_grant_unregistered_or_wildcard`.
- **selected:** `fullmatch`.
- **rejected:** stripping, which silently changes identity.
- **tradeoffs:** none.
- **reversal criteria:** none expected.
