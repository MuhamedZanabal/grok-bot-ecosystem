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

## R-0009: Isolation mechanisms not chosen for cycle-004
- **decision_id:** R-0009
- **date:** 2026-10-20
- **problem:** Alternatives to bubblewrap (D-0013).
- **options / evidence:**
  - **Dedicated low-priv UID (`setpriv`/`sudo -u`):** it needs root or sudo at runtime (sudo works on the box, but a runtime sudo dependency is a privilege the agent should not hold), it gives no network or mount isolation by itself, and it does not kill setsid descendants. Kept as the reversal path.
  - **`unshare` CLI alone:** it lacks bind-mount/tmpfs assembly without root (`--map-root-user` plus manual mounts); that is re-implementing bwrap in shell.
  - **Docker/podman:** a daemon or extra runtime, image management, and about 100s of ms per spawn (UNKNOWN, not measured); too heavy for per-call isolation.
  - **A setuid helper of our own:** new privileged code in the TCB.
  - **bwrap `--unshare-all` / `-try` variants for net or pid:** `-try` silently degrades. Only cgroup uses `-try`, because it is not a security boundary here (PID namespace teardown does the descendant kill). Net, pid and user are mandatory and the probe asserts no network.
  - **seccomp:** deferred. bwrap `--seccomp` needs a compiled BPF program; stdlib has no generator (cycle-010).
- **selected:** bubblewrap.
- **rejected:** the above.
- **tradeoffs:** no syscall filter.
- **reversal criteria:** see D-0013.

## R-0010: Single-layer `worker_kill_pid_not_group` mutant retired as equivalent
- **decision_id:** R-0010
- **date:** 2026-10-20
- **problem:** After the sandbox, the full mutation run gave `worker_kill_pid_not_group: SURVIVED` (`MUTATION mutants=42 killed=41`).
- **options:** keep a surviving mutant (CI red); weaken a test; or recognise an equivalent mutant and mutate both layers.
- **evidence:**
  - With `proc.kill()` instead of `killpg`, only `bwrap` is SIGKILLed, but its PID-namespace init dies too and the kernel kills every namespace member, so `wait_gone(("sleep","3599"))` stays `[]`. Defence in depth makes the single mutation behaviourally equivalent.
  - The replacement `worker_kill_pid_not_group_no_pidns` (killpg→kill **and** drop `--unshare-pid`) gave `killed` (`MUTANT_ONLY='worker_kill' bash scripts/mutation_check.sh`).
- **selected:** replace it with the two-layer mutant. Each layer alone is covered: `sandbox_no_pidns` is killed by the adversarial setsid and `/proc` cases.
- **rejected:** keeping it as a known survivor.
- **tradeoffs:** `killpg` alone is no longer mutation-verified; it is redundant defence.
- **reversal criteria:** the PID namespace becomes optional on any platform.

## R-0011: Egress designs not chosen
- **decision_id:** R-0011
- **date:** 2026-10-20
- **problem:** Alternatives to the Unix-socket filtering proxy (D-0015).
- **options / evidence:**
  - **(e2) Host netns + nftables/iptables:** needs root or CAP_NET_ADMIN at runtime, filters by IP (not hostname), and DNS stays inside the worker (exfil channel); any rule error opens the network.
  - **(e3) slirp4netns/pasta:** a new system dependency (not installed: UNKNOWN on CI); it gives the worker a full IP stack and DNS, and filtering is coarse.
  - **(e4) A TCP loopback proxy on the host:** unreachable from `--unshare-net` anyway, and if the netns were shared, every host loopback service would be exposed.
- **selected:** e1.
- **rejected:** e2-e4.
- **tradeoffs:** tools must use the CONNECT protocol (the builtin client does this); unmodified programs cannot use the network.
- **reversal criteria:** a capability needs unmodified network programs → an in-sandbox forwarder that still terminates in the same proxy.

## R-0012: Playwright and CDP-direct Chrome deferred for cycle-005
- **decision_id:** R-0012
- **date:** 2026-10-20
- **problem:** Browser engine selection (D-0015 b1/b2).
- **options / evidence:** D-0015 evidence. Playwright is 136 MiB with a bundled Node runtime and a CDN browser download. Chrome is 443 MiB, needs `--no-sandbox` inside bwrap, takes about 2 s per launch, and its background network behaviour is UNKNOWN. Neither was installed or run beyond the single `--dump-dom data:` probe of the preinstalled system Chrome.
- **selected:** defer both.
- **rejected:** b1 and b2 for this cycle. No in-scope capability needs JavaScript, and the stdlib-only preference applies.
- **tradeoffs:** JS-dependent sites return their pre-render HTML only.
- **reversal criteria:** see D-0015.

## R-0013: Memory designs not chosen (cycle-006)
- **decision_id:** R-0013
- **date:** 2026-10-20
- **problem:** Alternatives in D-0018.
- **options / evidence:**
  - **JSONL store:** no index and no atomic dedup.
  - **Unkeyed per-record SHA-256:** recomputable by anyone with DB write access. The `_tamper_rehash` case shows a rehashed forgery is caught only because the MAC is keyed (`reason mac_mismatch`).
  - **A sandboxed memory worker:** workers cannot reach host files by design (S-0006); a DB channel into the sandbox would widen worker reach.
  - **An embedding/vector index:** needs a model or third-party dependency (stdlib-only rule) and no in-scope capability needs semantic recall.
- **selected:** D-0018.
- **rejected:** the above.
- **tradeoffs:** lexical (FTS5/BM25) recall only.
- **reversal criteria:** a research cycle shows lexical recall is insufficient (measured) → evaluate an embedding index as a deferred, audited dependency.

## R-0014: Research-engine designs not chosen (cycle-007)
- **decision_id:** R-0014
- **date:** 2026-10-20
- **problem:** Alternatives in D-0020 and D-0021.
- **options / evidence:**
  - **URL normalization for source ids:** it would merge `…/alpha`, `…/alpha/` and `…/Alpha` (the `research_url_normalised` mutant shows the tests detect this) and hide URL mutation.
  - **Claims citing URLs only:** fabricated citations would be undetectable. The `fabricated_*` cases need `record_id`, the hash and the quote.
  - **Model-first extraction:** non-deterministic, and it makes the model the source of truth. Tier 1 runs only when Tier 0 finds no metric, and the verifier still applies.
  - **Letting `accepted` imply trust or executability:** it conflicts with Source Handling. The `CHECK (trusted = 0)` and `CHECK (execution_allowed = 0)` constraints make it impossible even with direct SQL.
  - **Third-party research or RAG frameworks:** they would violate the stdlib-only rule, and no measured need exists.
- **selected:** D-0020 and D-0021.
- **rejected:** the above.
- **tradeoffs:** a lexical, regex Tier 0 handles only `attribute=value` text.
- **reversal criteria:** a measured research task where Tier 0 + Tier 1 literal quoting recovers too few claims.

## R-0015: Scheduler designs not chosen (cycle-008)
- **decision_id:** R-0015
- **date:** 2026-10-20
- **problem:** Alternatives in D-0022 and D-0023.
- **options / evidence:**
  - **Jobs as an agent-callable capability:** CP handlers receive args only, not the calling actor. A `job_define` capability therefore could not bind the owner safely, so an agent could define a job running as anyone. The `privilege_escalation_definitions` case shows the host API refuses 8 escalation shapes.
  - **Retrying in-doubt steps:** that is double execution when the first attempt had its effect. The `crash_mid_step` case requires `in_doubt`, and the test shows `begins {0: 1, 1: 1}`.
  - **Catching up on every missed slot:** unbounded work after an outage. `forward_jump` caps a 1-year jump at 3 runs plus 1 gap row.
  - **Trusting the wall clock monotonically:** a backwards jump could re-open slots. The per-job `high_water` prevents that (`backwards_clock` case).
  - **Third-party schedulers (APScheduler, celery beat):** non-stdlib, and they do not route through ControlPlane idempotency.
- **selected:** D-0022 and D-0023.
- **rejected:** the above.
- **tradeoffs:** `in_doubt` runs need an operator.
- **reversal criteria:** a workload that needs at-least-once semantics, together with idempotent downstream effects proven per capability.

## R-0016: Economics designs not chosen (cycle-009)
- **decision_id:** R-0016
- **date:** 2026-10-20
- **problem:** Record the alternatives rejected in D-0024 and D-0025.
- **options / rejected:**
  - Economics columns on the CP idempotency ledger (not MAC'd; mixes integrity domains).
  - Floats defaulting to 0 (fabricates numbers).
  - Caller- or handler-supplied `actual_revenue` (forgeable). Workers' self-reported byte counts (untrusted). Only proxy-side `egress_closed` is used.
  - Post-hoc spend alerts instead of pre-execution denial.
  - Per-row MAC re-verification on every admit: O(rows today), and it missed tampering of non-reservation rows. Replaced by `data_version`-triggered full verification.
  - Mutant `econ_not_atomic` (`BEGIN IMMEDIATE` → `DEFERRED`): **retired as equivalent in-process**. The ledger's Python lock already serializes admits within one process. `BEGIN IMMEDIATE` matters only across processes, which no test exercises (S-0011 gap 4).
  - A `python -m control_plane status` entry point (would couple the CP to scheduler, memory and research imports).
- **evidence:** D-0025; `ECON_ADVERSARIAL` case `forged_revenue_via_db` failed against the per-row design and passes against the final one.
- **reversal criteria:** a multi-process deployment (then add a cross-process race test and restore the mutant).

## R-0017: Outreach designs not chosen (cycle-010)
- **decision_id:** R-0017
- **date:** 2026-10-20
- **problem:** Record the alternatives rejected in D-0026 and D-0027.
- **options / rejected:**
  - Committing real lead CSVs to the repo (third-party contact data in git history). Only synthetic `example.invalid` fixtures are committed.
  - Model-written drafts (can invent facts).
  - Using the source CSV's `fit_score` (untrusted, unexplained).
  - Storing the `+tag`-stripped address as the recipient (would change who receives mail). It is used only for keys and caps.
  - Mode in the operation id only (a dry run in a dry deployment would consume the live op id).
  - An `approve` CLI (would let any process with box access mint tokens). Tokens are minted only by the approval authority holder, outside this cycle.
  - Wiring Gmail, Agentmail or WhatsApp transports (forbidden by the user this cycle).
- **reversal criteria:** the user explicitly authorizes a real transport, with its own cycle and tests.
