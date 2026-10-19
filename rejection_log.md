# Rejection log (append-only)

## R-0001: Third-party agent/policy frameworks for Cycle 1
- **decision_id:** R-0001
- **date:** 2026-10-19
- **problem:** Whether to adopt an external policy engine or agent framework for the control plane.
- **options:** external policy engine (e.g. OPA), agent framework guardrails, or a small stdlib implementation.
- **evidence:** the operating spec treats external code as untrusted and asks for no third-party frameworks in Cycle 1. The stdlib implementation meets the success metric (`python3 -m unittest discover -s tests -t .` -> 52 OK; adversarial 20/0; mutation 10/10). No benchmark of the alternatives was run, so their relative merit is UNKNOWN.
- **selected:** stdlib implementation (`src/control_plane/`, 788 lines).
- **rejected:** external engines and frameworks, deferred and not evaluated.
- **tradeoffs:** we own correctness; the policy language is code, not declarative.
- **reversal criteria:** policy rules outgrow per-actor grants (e.g. attribute or time conditions) and an engine shows a measurable advantage in a benchmark.

## R-0002: Ruff (or any pip-installed linter) in CI for Cycle 1
- **decision_id:** R-0002
- **date:** 2026-10-19
- **problem:** Lint step in CI.
- **options:** `python -m compileall` (stdlib) or `pip install ruff`.
- **evidence:** `.github/workflows/ci.yml` runs `python -m compileall -q src tests bench`.
- **selected:** compileall.
- **rejected:** ruff, deferred to avoid adding a downloaded tool to the supply chain in the first cycle.
- **tradeoffs:** compileall only catches syntax errors, not style or unused code.
- **reversal criteria:** Cycle 9 adds pinned, hash-verified dev tooling.
