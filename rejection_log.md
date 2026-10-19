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

## R-0003: Third-party agent frameworks for orchestration
- **decision_id:** R-0003
- **date:** 2026-10-19
- **problem:** Whether to adopt an agent framework for Cycle 2.
- **options:** an external agent framework, or the stdlib runner (`src/orchestrator/`, 554 new lines including grant store and gateway).
- **evidence:** the stdlib runner meets all six success criteria (D-0004; see integration_log.md I-0004). No framework was benchmarked, so comparative merit is UNKNOWN.
- **selected:** stdlib runner.
- **rejected:** frameworks are deferred, not evaluated. Spec: external code is untrusted, and frameworks typically pass tool callables to model-adjacent code, which conflicts with D-0006.
- **tradeoffs:** we write our own provider adapters.
- **reversal criteria:** a framework offers a capability we need (e.g. streaming or multi-agent handoff) that is measurably cheaper than building it, and can sit behind `ModelProvider` without receiving handlers.

## R-0004: Vendor-native tool-calling schemas
- **decision_id:** R-0004
- **date:** 2026-10-19
- **problem:** Use vendor function-calling formats instead of a JSON text protocol.
- **options:** native tool calling, or the JSON text protocol (`runner.PROTOCOL`).
- **evidence:** no real provider adapter exists in Cycle 2, so there is no parse-failure data. UNKNOWN.
- **selected:** text protocol.
- **rejected:** deferred.
- **tradeoffs:** possibly higher malformed-output rates with real models (UNKNOWN; not measured).
- **reversal criteria:** measured malformed rate above 2% on a real adapter.

## R-0005: Working around unavailable branch protection
- **decision_id:** R-0005
- **date:** 2026-10-19
- **problem:** Branch protection on private `main` returned HTTP 403 (S-0004).
- **options:** make the repo public, try repository rulesets or another API route, upgrade the plan, or accept and record the gap.
- **evidence:** `gh api -X PUT repos/MuhamedZanabal/grok-bot-ecosystem/branches/main/protection` gave `"Upgrade to GitHub Pro or make this repository public to enable this feature." (HTTP 403)`.
- **selected:** record the gap; no workaround attempted.
- **rejected:** making the repo public (the user asked for private); alternate API routes (instructed not to work around); upgrading (a purchase needs the user).
- **tradeoffs:** merges are not technically gated on CI. This is procedural only: the agent re-checks `gh pr checks` before every merge.
- **reversal criteria:** the user upgrades the plan or chooses to make the repo public.

## R-0006: MCP SDK / live MCP transport in Cycle 3
- **decision_id:** R-0006
- **date:** 2026-10-19
- **problem:** Connect to real MCP servers.
- **options:** an MCP SDK (third-party), a hand-written stdio/HTTP JSON-RPC client, or descriptor import only.
- **evidence:** task scope: no real MCP connections or network in tests. `src/tools/mcp_adapter.py` parses `tools/list` results only. Whether real servers' `inputSchema`s fit the subset (no `$ref`, `oneOf`, `format`) is UNKNOWN. Such tools are rejected at import with `unsupported_schema:*`, not silently accepted.
- **selected:** descriptor import only.
- **rejected:** SDK and transport, deferred.
- **tradeoffs:** accepted MCP tools currently need an operator-supplied local `WorkerSpec` implementation.
- **reversal criteria:** a cycle that needs live MCP tools implements a stdio JSON-RPC client *inside* a `SubprocessHandler` worker, so transport code is isolated too.

## R-0007: In-process sandboxing (restricted builtins/`exec`)
- **decision_id:** R-0007
- **date:** 2026-10-19
- **problem:** Isolate tool code without processes.
- **options:** restricted `exec`, or OS processes.
- **evidence:** none collected (not benchmarked). Python restricted execution has a long history of escapes, and the CPython docs do not offer a sandbox.
- **selected:** OS processes (D-0009).
- **rejected:** in-process sandboxing.
- **tradeoffs:** spawn cost.
- **reversal criteria:** none.

## R-0008: `close_fds=False` mutant omitted from the mutation check
- **decision_id:** R-0008
- **date:** 2026-10-19
- **problem:** Whether to add a mutant that disables `close_fds`.
- **options:** add it, or omit it with rationale.
- **evidence:** Python opens files non-inheritable by default (PEP 446), so `close_fds=False` alone would likely not leak the ledger/audit fds. The mutant would probably survive without indicating a missing test. Not run, so its outcome is UNKNOWN.
- **selected:** omit; keep `close_fds=True` as defence in depth, with the fd-inheritance test asserting the outcome.
- **rejected:** an equivalent-mutant that adds noise.
- **tradeoffs:** `close_fds` itself is not mutation-verified.
- **reversal criteria:** any code path that creates inheritable fds (e.g. `os.set_inheritable`) is added.
