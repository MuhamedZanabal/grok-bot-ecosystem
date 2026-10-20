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

## S-0003: Cycle 2 orchestration boundary and remaining in-process gap
- **decision_id:** S-0003
- **date:** 2026-10-19
- **problem:** S-0001 gaps 4 (grants not persisted) and 5 (in-process bypass). Agents must not be able to reach handlers.
- **options:** see D-0006.
- **evidence:**
  - **Gap 4 closed:** `src/control_plane/grant_store.py`; `tests/test_grant_store.py` covers restart persistence for grant and revoke, monotonic versions, and complete history. Every grant and revoke writes `policy_grant`/`policy_revoke` audit records with `policy_version` and `by`. Execution decisions record `policy_version`. A grant is bound to the risk level at grant time, so re-registering a capability at a higher risk gives `deny:grant_risk_mismatch` (`test_risk_escalation_invalidates_grant`; mutant `skip_grant_risk_check` killed).
  - **Model boundary:** the model controls only text. 18 adversarial model outputs (ungranted L1/L3, L2 without approval, smuggled `approval_token`, `actor` override, duplicate-key smuggling, newline bypass, `"type":"handler"`, Python code, unknown kill-release capability, NaN, oversize, depth bomb, concatenated objects, retry-forever, prompt injection via tool result, approved-key reuse with different args, fence plus trailing payload) gave `ORCH_ADVERSARIAL cases=18 unauthorized_executions=0`. Prompt injection via a tool result stops at `awaiting_approval`, and the L2 handler is not called.
  - **Runtime boundary:** `AgentRunner` accepts only an `ActorGateway`, which is data-only, immutable, unpicklable and uncopyable, with the actor fixed at construction (`HandlerIsolationTests`, 7 tests).
  - **Gap 5 partially open:** the gateway is a *data-flow* boundary, not a memory boundary. Python code running in the same process can reach handlers by introspection, e.g. `object.__getattribute__(gw, "_execute").__self__._handlers`. `test_known_gap_in_process_introspection` pins this so that closing it is a visible change. Today this matters only for trusted in-process adapter code, not for model output.
- **selected:** text-only model channel plus gateway now; out-of-process handlers in Cycle 3 (`cap.tools.handler_isolation`).
- **rejected:** claiming full isolation, because it is not true in-process.
- **tradeoffs / open gaps:**
  1. In-process introspection (above).
  2. Timed-out provider threads, like handler threads, keep running.
  3. The model sees handler results in its transcript (truncated to `max_observation_bytes`), so tool output is a prompt-injection vector. It is mitigated only by approvals for risk >= 2, not by content filtering.
  4. The grant store has no authentication: anyone with write access to the SQLite file can grant. File permissions and key custody are Cycle 9.
  5. Raw model output is not persisted (hash and length only), which limits forensic replay.
- **reversal criteria:** any adversarial or mutation regression blocks merge.

## S-0004: Branch protection unavailable on private repo (current plan)
- **decision_id:** S-0004
- **date:** 2026-10-19
- **problem:** Require CI (`test (3.11)`, `test (3.12)`, `test (3.13)`) before merges to `main`.
- **options:** classic branch protection API, or record the gap (see R-0005).
- **evidence:** `gh api -X PUT repos/MuhamedZanabal/grok-bot-ecosystem/branches/main/protection --input <required_status_checks strict, contexts 3.11/3.12/3.13; no force-push; no deletion>` gave `{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","status":"403"}`.
- **selected:** known gap, recorded and not worked around.
- **rejected:** see R-0005.
- **tradeoffs:** `main` can be pushed to directly or merged red by anyone with write access. The mitigation is procedural only (the agent checks `gh pr checks` before merge, as done for PR #1).
- **reversal criteria:** the plan is upgraded or the repo is made public, at which point the same API call is applied.

## S-0005: Cycle 3 tool isolation and what remains open
- **decision_id:** S-0005
- **date:** 2026-10-19
- **problem:** Close S-0003 gap 1 (handlers reachable in-process) and add argument validation.
- **options:** see D-0008 to D-0011.
- **evidence:**
  - **S-0003 gap 1 closed for tool code:** `tests/test_orchestrator_adversarial.py::test_gap_closed_tool_code_cannot_reach_handlers` (the flipped pinned test). On a strict control plane every bound handler is a `SubprocessHandler` holding only a frozen `WorkerSpec`; the tool module never loads in the parent; in the worker, `importable=[]`, `loaded=[]`, `gc_hits=0`.
  - **Env:** the worker env is exactly `{PATH, LANG}` plus explicit non-credential names. `GITHUB_TOKEN`, `OPENAI_API_KEY`, `AWS_SECRET_ACCESS_KEY`, `DB_PASSWORD` and `SESSION_ID` are refused at `WorkerSpec` creation (mutant `worker_sensitive_env_ok` killed; `worker_env_inherit` killed).
  - **Timeout:** the process group is SIGKILLed, and the worker and its child are verified dead via `/proc/<pid>/stat`.
  - **rlimits:** a 4 GiB allocation under a 256 MiB limit gives `handler_error:MemoryError`; a CPU spinner under `cpu_s=1` gives `worker_crashed:rc=-N`.
  - **Protocol:** tool prints to stdout and writes to inherited fds 3-9 cannot replace the real reply (`test_tool_stdout_and_fd_forgery_cannot_replace_reply`). Worker error codes are untrusted and are reduced to `[A-Za-z0-9_:=.-]{1,100}` or `worker_error`; secrets in exception messages never reach the audit log.
  - **Schemas:** `TOOL_ADVERSARIAL` covers 10 schema-bypass cases (extra property, bool or float as integer, bounds, newline/prefix pattern smuggling, enum, length, nested extra, missing required), all `0` executions.
  - **Finding (new, fixed in this cycle):** a same-UID worker could read the host's startup environment via `/proc/<ppid>/environ`. Evidence: `HARDENING unhardened={'hardened': False, 'readable': True, 'canary': True}`. Mitigation: `tools/hardening.py::set_non_dumpable()` (`prctl(PR_SET_DUMPABLE, 0)`) gives `hardened={'hardened': True, 'readable': False, 'canary': False}` (`HostHardeningTests`). Note `os.environ` changes made after startup do not appear in `/proc/<pid>/environ` (`RESIDUAL ... setenv_canary_visible=False`), so the exposure is the *startup* environment.
- **selected:** subprocess isolation plus host hardening, with schemas enforced before replay, approval and execution.
- **rejected:** claiming a sandbox.
- **open gaps:**
  1. **Not a sandbox.** Workers run as the same UID, with full filesystem and network access subject to OS permissions. A tool can read or write any file the agent user can, including the ledger, audit and grant SQLite files, by path. OS-level isolation (separate uid, mount/network namespaces, seccomp) is Cycle 9 (`cap.tools.os_sandbox`).
  2. `set_non_dumpable()` is opt-in and must be called by the host at startup. Nothing enforces that today.
  3. A tool child that calls `setsid()` leaves the process group and survives the kill.
  4. Trusted in-process code (runner, adapters, the TCB) can still call launchers directly and skip policy (`test_residual_trusted_inprocess_code_can_invoke_launchers`). The model cannot, and tool code cannot.
  5. Registry trust state is in memory; only audit records persist.
  6. MCP descriptions are untrusted text and still reach the model's catalog once a tool is accepted and granted (prompt-injection vector).
  7. rlimits are Linux-specific. On other platforms the worker limits are UNKNOWN (best-effort) and only the wall-clock kill applies.
- **reversal criteria:** any adversarial or mutation regression blocks merge.

## S-0006: cycle-004-isolation, OS sandbox: what closed and what remains
- **decision_id:** S-0006
- **date:** 2026-10-20
- **problem:** Close S-0005 gaps 1, 2, 3 and 5 before credential-bearing cycles (RM-0003).
- **options:** D-0013, D-0014.
- **evidence:** `tests/test_sandbox_adversarial.py` gave `SANDBOX_ADVERSARIAL cases=21 escapes=0`, judged host-side. The cases:
  - read the ledger, grant DB and audit log by host path; read `~/.config/gh/hosts.yml`, `/etc/passwd` and host `/proc/<parent>/environ`;
  - list home and the repo checkout;
  - write into the tool root, `/usr`, a host temp dir and `/worker/worker.py`; a `/tmp` write lands only in the private tmpfs;
  - TCP to `1.1.1.1:53`; TCP to a host loopback listener (0 accepts); a host Unix socket; DNS;
  - see host processes (`/proc` shows ≤3 PIDs, not the parent);
  - a minimal root filesystem;
  - a setsid plus double-fork daemon surviving a timeout; a setsid child surviving a normal return.
- **closed:**
  - **S-0005 gap 1 (filesystem/network reach):** workers see no host state files, home or workspace, and have no network. Evidence: the cases above. Mutants `sandbox_no_netns`, `sandbox_bind_host_root` and `sandbox_tool_root_writable` were killed.
  - **S-0005 gap 2 (hardening opt-in):** now enforced in `ControlPlane()` (D-0014). It is also moot for workers, since even an unhardened host leaks nothing (`SANDBOX_PROC unhardened_host={'ok': False, 'visible_pids': 2, ...}`).
  - **S-0005 gap 3 (setsid escape):** the PID namespace kills the whole tree. `wait_gone(sleep 3596..3599) == []`. Mutant `sandbox_no_pidns` was killed.
  - **S-0005 gap 5 (registry in memory):** persisted, versioned and audited (D-0014).
  - **Fail closed:** no bwrap, an untrusted bwrap or a failing probe means no tool runs. Mutants `sandbox_trust_any_bwrap` and `sandbox_probe_ignored` were killed.
- **remaining gaps:**
  1. No seccomp syscall filter; the kernel attack surface is reachable from the userns (R-0009).
  2. CI relaxes AppArmor's unprivileged-userns restriction if needed (D-0013). Production hosts need userns enabled; on hardened hosts tools refuse to run.
  3. Trusted in-process code can still call launchers directly (`test_residual_trusted_inprocess_code_can_invoke_launchers`, still pinned).
  4. The registry, grant and ledger SQLite files are not integrity-protected against a same-UID host process (not reachable from workers). Store signing is cycle-010.
  5. MCP descriptions are still untrusted text reaching the model (S-0005 gap 6).
  6. Non-Linux: tools cannot run (fail closed). Whether `cgroup` namespacing applies on a given kernel is UNKNOWN (`--unshare-cgroup-try`); it is not relied upon.
  7. No memory or PID-count cgroup limits; rlimits apply per process only (`RLIMIT_NPROC` not set, so a fork bomb inside the sandbox is bounded only by the wall-clock kill plus host limits; UNKNOWN impact, not tested).
  8. Branch protection is unavailable (S-0004).
- **reversal criteria:** any escape or a mutant regression blocks merge.

## S-0007: cycle-005: egress, fork limit, browser fetch
- **decision_id:** S-0007
- **date:** 2026-10-20
- **problem:** Give sandboxed tools network access without giving up deny-by-default, and close S-0006 gap 7 (no process-count limit).
- **options:** D-0015, R-0011, R-0012.
- **evidence:** `tests/test_egress_adversarial.py` (26 cases, judged host-side) gave `unauthorized_egress=0`, `proxy_attempts=22 audited_attempts=22 audit_chain_ok=True`. The cases:
  - **Not allowlisted:** non-allowlisted port, IP and name (never resolved).
  - **DNS exfil:** via a proxy hostname, with a canary label never resolved; via the direct resolver (no network).
  - **Bypass:** direct TCP around the proxy.
  - **Malformed CONNECT lines (9):** CRLF-smuggled second CONNECT, uppercase host, leading-zero port, userinfo, IPv6 literal, trailing token, overlong line, non-ASCII, empty line.
  - **Rebinding guard:** an allowlisted `localhost` resolving to 127.0.0.1 gives `resolved_non_global`.
  - **Redirects:** to a blocked host, and to a blocked port with the canary in the query. The blocked fixture got 0 hits.
  - **Budgets:** connection budget, and a byte budget (`budget_exhausted=True` audited).
  - **Configuration:** a capability without egress has no socket; `file:`/`ftp:`/`gopher:` are refused; egress at risk READ is refused at registration; egress without an audit sink is refused; wildcard, CIDR and port-less entries are refused.
  - **Fork bomb:** bounded (30 forks with `max_procs` 32; the bomb peaked at 15 with a limit of 16), killed at the timeout, 0 left over.
- Mutants (12 new) kill each guard: allowlist skipped, resolve-before-allowlist, rebinding guard, connection budget, byte budget, lenient grammar, deny or allow not audited, egress without audit, entry validation, low-risk egress, and NPROC removed.
- TLS verification is on (`tls_verify_failed` for a self-signed fixture), with real CA roots inside the sandbox.
- **selected:** integrate.
- **remaining gaps:**
  1. **Exfiltration to an allowlisted host is permitted by design.** A tool or model can put data in URLs or bodies sent to an allowlisted destination. The mitigations are only the narrow allowlist, the approval gate (risk 2), the byte budget and the audit.
  2. **Page content is untrusted and flows back to the model** (prompt injection). There is no content filtering (cycle-010).
  3. **The proxy is new TCB code in the host process** (threads, sockets). A DoS by a worker is bounded by the connection and byte budgets plus the worker timeout. Read-line timeout is 5 s per connection and joins are bounded at 2 s per thread; that was not fuzzed.
  4. **The rebinding guard is check-then-connect on the resolved IP** (we connect to the IP we checked, so there is no TOCTOU between them). IPv6 is not supported (AF_INET only).
  5. **IPv4-literal allowlist entries bypass the non-global check by design.** Operators can allowlist internal IPs, and that is an explicit choice.
  6. **No JavaScript engine.** JS-heavy sites return pre-render HTML only (R-0012).
  7. **`RLIMIT_NPROC` semantics depend on per-userns ucounts (kernel ≥ 5.14).** On older kernels it would count all host processes of the UID, which is safer but could break tools. Verified here (6.12) and in CI (runner kernel version recorded with the PR run).
  8. **Credential broker: designed only (RM-0006).** No credentials are reachable by any tool.
  9. Earlier gaps carried over: no seccomp; trusted in-process launchers; unsigned SQLite stores; CI relaxes AppArmor userns; no branch protection.
- **reversal criteria:** any unauthorized egress or mutant regression blocks merge.

## S-0008: cycle-006 memory: what holds and what remains
- **decision_id:** S-0008
- **date:** 2026-10-20
- **problem:** Persisting external content must not create an instruction channel, a secret store, or silently accepted tampered data.
- **options:** D-0018, D-0019, R-0013.
- **evidence:**
  - **Poisoned memory:** `POISONED_MEMORY proposed=5 beyond_policy=5 statuses=['denied', 'denied', 'denied', 'rejected', 'denied'] dangerous_executions=0 trusted_records=0 state=completed`. A deliberately gullible model proposed every injected action (payment transfer, email, fs write, a trust-escalating memory write, an unknown admin capability), and all were refused by policy or schema.
  - **`MEMORY_ADVERSARIAL cases=15 violations=0`:**
    - poisoned run;
    - budgets: oversized (both layers), store bytes, record count;
    - secrets: in content (3 redactions; raw DB bytes and audit log contain no secret); in the URL (refused, not stored);
    - tampering: content (`content_hash_mismatch`); content plus recomputed hash (`mac_mismatch`); trust flip (`mac_mismatch`); deleted row (`chain_break`);
    - duplicates (dedup plus idempotent replay, 2 audited writes);
    - trust escalation via args (`rejected`);
    - expiry (excluded, tombstoned, chain still verifies);
    - FTS injection;
    - wrong key (all excluded).
  - 14 new mutants, all killed (`MUTANT_ONLY='^memory'` gave `14/14`).
- **remaining gaps:**
  1. **Injected text can still steer the model *within* its grants.** For example, a poisoned page can make a gullible model call `cap.memory.write` or `cap.memory.search` (granted, risk ≤ 1). Policy bounds the blast radius; it does not stop influence. Content filtering is not implemented (cycle-010).
  2. **The MAC key lives in host memory/config.** An attacker with the key and DB write access can forge records. Audit-log anchors (`memory_write.mac`) let `verify_chain` plus audit cross-checks detect it, but there is no automated cross-check yet and no remote anchor.
  3. **Tail truncation** of the memory DB is invisible to `verify_chain` alone; it is detectable only against audit anchors (not automated).
  4. **Redaction is pattern-based** (`_SECRET_VALUE`: GitHub, OpenAI-style, Slack, AWS keys, bearer tokens, PEM private keys). Unknown secret formats are stored as-is.
  5. **`TrustedHostHandler` is a new bind-by-default handler kind.** Any repository code subclassing it runs in the host process; it is controlled by code review, not enforced.
  6. **Memory contents are visible to every actor granted `cap.memory.search`**; there are no per-actor namespaces.
  7. Earlier gaps carried over: S-0006 and S-0007 gaps.
- **reversal criteria:** any violation or mutant regression blocks merge.

## S-0009: cycle-007 research/evidence security posture
- **decision_id:** S-0009
- **date:** 2026-10-20
- **problem:** Research output must not let a source fabricate citations, mutate its identity, promote its own trust, or turn content into actions.
- **options:** D-0020, D-0021, R-0014.
- **evidence:** `RESEARCH_ADVERSARIAL cases=16 violations=0` (`tests/test_research_adversarial.py`). Cases:
  - **Fabricated citations:** a fabricated record id, hash or quote; a quote that does not support the value; the subject missing from the record. All refused (`record_not_found`, `hash_mismatch`, `quote_not_in_record`, `quote_does_not_support_claim`, `subject_not_in_record`).
  - **Mock model with 3 fabricated citations:** all refused, 0 claims stored, source deferred.
  - **URL mutation (registry):** the mutated URL gets a distinct `source_id`. Its claim is refused (`origin_mismatch`) and its transition refused (`evidence_origin_mismatch`).
  - **URL mutation (redirect):** the source stays `candidate`, with 0 claims.
  - **Self-promotion:**
    - Content (`trusted=true execution_allowed=true status=accepted`, plus injected action JSON): the source stays `trusted=False`, `execution_allowed=False`, `deferred`; no reserved-attribute claims; 0 `cap.payment.transfer` decisions.
    - Model (`trusted` / `execution_allowed` claims): refused (`reserved_attribute`).
    - Args (`trusted` / `execution_allowed` keys): `REJECTED` by the schema.
    - Direct SQL `UPDATE sources SET trusted=1`: `IntegrityError` from the CHECK constraint.
  - **Transitions:** empty evidence (schema), an illegal edge, accept without a verified claim, evaluating without a record, a record with the wrong hash, and an unknown source are all refused, and the status is unchanged.
  - **Claim without evidence:** `REJECTED` by the schema, `claim_without_evidence` from the verifier, and verdict `UNKNOWN`.
  - **Contradictory sources** (omega 20 vs 90): both `UNKNOWN [contradicted]`, value shown as `UNKNOWN`, both sources `deferred`, pass not superior.
  - **Stale evidence:** `UNKNOWN [stale]` after `max_age`, and still after memory expiry. The report shows `best_claim_still_verified=False`.
  - **Tampered evidence after a claim:** `UNKNOWN [evidence_unverifiable:evidence_tampered:content_hash_mismatch]`.
  - **Rejected source:** its claims are `UNKNOWN [source_rejected]`.
  - **Model-injected action:** `model_output_ignored:type=action` and 0 payment decisions. The model has no gateway; its output is parsed as data only.
  - **Mutants:** 26 new, all killed (`MUTANT_ONLY='^research_'` gave `26/26`).
- **remaining gaps:**
  1. **Claim ledger rows are not MAC'd.** Every read re-verifies them against MAC'd memory, so a forged row still needs a real record and a literal quote. A DB writer could, however, delete claims, for example to hide a contradiction. There is no ledger chain.
  2. **Quotes support claims syntactically only** (`attribute` and `value` tokens in the quote). A page saying `p50_ms=40 (on a different machine)` yields a "verified" claim. "Verified" means "faithfully cited", not "true".
  3. **Contradiction is exact string inequality on `(subject, attribute)`.** `40` vs `40.0` counts as a contradiction, and unit-mismatched equal values do not.
  4. **Each fetch needs an operator approval token** (EXTERNAL risk). Tests mint them with the harness key; there is no operator UI.
  5. **A single compromised page can still block a candidate** by publishing a contradicting value. This is a denial of evidence, not an escalation, and the result is `UNKNOWN`, which is correct but exploitable.
  6. **The automated memory audit-anchor cross-check (RM-0008) and per-actor namespaces were not done** (deferred; scope).
  7. Earlier gaps carried over: S-0008 gaps 1-7.
- **reversal criteria:** any violation or mutant regression blocks merge.

## S-0010: cycle-008 evidence hardening + scheduler security posture
- **decision_id:** S-0010
- **date:** 2026-10-20
- **problem:**
  - Evidence stores must reveal deletions and edits.
  - Scheduled work must never run twice, never run with more privilege than its definer had, and must stop on the kill switch.
- **options:** D-0022, D-0023, R-0015.
- **evidence:**
  - **Evidence tampering:** `LEDGER_TAMPER detected=6/6`. A tampered store turns every claim in the report to `UNKNOWN`. `CROSSCHECK tampers=6 detected=6 failures=[]` runs in CI.
  - **`SCHEDULER_ADVERSARIAL cases=13 violations=0 max_begins_per_op=1`:**
    - **duplicate slot:** a second instance with a stale `high_water` hits the PK, logs `slot_already_claimed`, and runs nothing;
    - **restart storm:** 60 instances with periodic mid-step crashes; every run ends terminal, including `in_doubt`;
    - **escalation at definition, 8 refusals:**
      - EXTERNAL or IRREVERSIBLE step;
      - a `cap.scheduler.*` step;
      - an `actor` or `approval_token` key in a step;
      - an ungranted actor;
      - an unknown capability;
      - redefining an existing job;
    - **tampered job row** (actor changed to `system.admin`, payment step added): quarantined, 0 executions;
    - **wrong-key restart:** quarantined;
    - **grant revoked:** `failed`, handler not called;
    - **backwards clock:** audited once; old slots never re-open;
    - **kill switch mid-run:** `killed`, later step not executed, next tick halted, resumes after release;
    - **crash mid-step:** `in_doubt`; per-step begins are `{0: 1, 1: 1}`;
    - **crash between steps:** recovered, `succeeded`, begins `{0: 1, 1: 1}`;
    - **overlap:** `skipped_overlap`;
    - **1-year forward jump with catch-up cap 3:** 3 runs, gap `525597`;
    - **lease takeover race:** the compare-and-swap lets exactly one instance through.
  - **Mutants:** 24 new, all killed in the filtered run.
- **remaining gaps:**
  1. **Keys live in host memory/config.** That covers the job-store, claim and memory MAC keys. A key holder can forge a store row, but the audit anchors still expose it. The audit log itself is an unkeyed hash chain: someone who can rewrite the whole audit log *and* holds the store keys can forge consistently. There is no external anchor or witness yet.
  2. **Branch and pass tables** (`ledger.branches`/`passes`) and the scheduler `runs`/`gaps` rows are not MAC'd. Tampering with them can mislabel state, but it cannot re-execute a step, because CP idempotency is keyed by the operation id.
  3. **`in_doubt` needs an operator**; there is no operator UI or alert.
  4. **The scheduler runs steps synchronously in `tick`.** A slow step delays other jobs; concurrency limits apply across instances and stale leases, not threads.
  5. **Cron is UTC only**, with no DST or time-zone semantics. Cron gaps over 366 days report `skipped=-1` (UNKNOWN).
  6. **Lease length is a constant** (`lease_s`). A step that legitimately runs longer than its lease could be re-driven by another instance and would come back `in_doubt` (never executed twice, but spuriously in doubt).
  7. **The unattended ceiling is LOCAL_WRITE.** A scheduled job can still do anything its actor's granted risk ≤ 1 capabilities allow.
  8. Earlier gaps carried over: S-0008 and S-0009 gaps not closed here (redaction patterns, model steering within grants, no seccomp, no branch protection, CI relaxes AppArmor userns).
- **reversal criteria:** any violation or mutant regression blocks merge.

## S-0011: cycle-009 economics, spend guard and status security posture
- **decision_id:** S-0011
- **date:** 2026-10-20
- **problem:**
  - Economic numbers must never be fabricated.
  - Spend caps must deny before execution and survive splitting, rotation, restart and tampering.
  - Telemetry must not leak secrets.
- **options:** D-0024, D-0025, R-0016.
- **evidence:** `ECON_ADVERSARIAL cases=16 violations=0`:
  - **split bypass:** 20 tiny ops under a $0.30/day cap at $0.10 each → 3 executed;
  - **run-id rotation:** 20 fresh run ids, run cap $0.10, day cap $0.40 → 4 executed;
  - **restart:** a new ledger instance keeps the burn → denied;
  - **concurrent race:** 8 threads × 5 under `day_calls=10` → exactly 10;
  - **forged revenue via args or result:** ignored, revenue stays UNKNOWN;
  - **forged revenue via API:** refused and audited; estimated values rejected as actual meters;
  - **forged revenue via DB edit:** row hidden, chain and cross-check fail, guard denies `ledger_integrity`;
  - **UNKNOWN never 0:** unpriced under a cap → `cost_unknown`; unknown sums stay UNKNOWN in totals and Markdown;
  - **tamper:** middle delete, tail truncation (caught by audit anchors and the guard's head check), lowering reserved USD in place, and a forged insert by a key holder (flagged unanchored);
  - **secrets:** a secret in the handler failure reason, op id and scope ids appears in neither the ledger file nor the export;
  - **dry run:** no handler call, no rows, no ledger op, no cap consumption; still subject to caps;
  - **model calls:** unpriced model under a USD cap → run aborted before the provider is called; malformed usage fields (negative, bool, string) → UNKNOWN.

  Mutants: 28 new `econ_*`, all killed in the filtered run.
- **remaining gaps:**
  1. **Caps bound configured estimates, not billed spend.** There is no billing or provider invoice source, so real dollar spend is UNKNOWN, and a wrong price table under-caps.
  2. **Egress bytes are attributed per capability** over the operation window. Concurrent operations of the *same* capability can cross-attribute bytes; the total stays exact.
  3. **Provider `last_usage` is provider-reported.** A lying provider can under-report tokens; only shape is validated.
  4. **Cross-process atomicity is designed but untested.** `BEGIN IMMEDIATE` is used, but no multi-process race test exists (R-0016). The `data_version` re-verification is per connection.
  5. **The ledger MAC key lives in host memory/config** (as S-0010 gap 1). A key holder who also rewrites the audit log can forge consistently. There is no external witness.
  6. **The status report is read-only and pull-based.** There is no push alerting.
  7. **Revenue is unmeasurable by design** until a reconciler exists (RM-0011).
  8. S-0010 gaps 1, 2 and 4-8 carry over. Gap 3 (no operator view of `in_doubt`) is now partially closed by the status report.
- **reversal criteria:** any violation or mutant regression blocks merge.

## S-0012: cycle-010 outreach security posture
- **decision_id:** S-0012
- **date:** 2026-10-20
- **problem:** No message may reach anyone without a human approval for that exact draft, recipient, channel and mode. Lead data is untrusted and must not become text we did not vet, or an action.
- **options:** D-0026, D-0027, R-0017.
- **evidence:** `OUTREACH_ADVERSARIAL cases=15 violations=0`:
  - **no token** → `approval_required`.
  - **token reuse:** a token for draft A used on B → `approval_invalid`; a token reused with another recipient → `approval_invalid`; a validly signed token for a wrong recipient → the handler refuses (`recipient_or_channel_mismatch`); a dry-run token used live → `approval_invalid`; an unknown channel → `rejected` by schema.
  - **tamper after approval:** body edited → `draft_tampered` (MAC); a key holder re-MACs and re-hashes → `draft_hash_mismatch` (the token binds the old hash).
  - **suppression:** a recipient suppressed under a variant spelling after approval → refused; opt-out via the tracker after approval → refused, and pending drafts become `rejected`.
  - **cap splitting:** 8 approved drafts → 4 sent and 4 refused, then 4 the next day. 4 drafts to one mailbox under case and `+tag` variants and posing as 4 lead ids → 1 per day, 2 lifetime.
  - **double send:** replay → `replayed`; a new attempt → `not_pending:sent`; a re-enqueue under a fresh op id does not reset `sent`; `sends` rejects a second row.
  - **injection** (instruction text in name, bidi/zero-width chars, `<script>` in area, injected category/cuisine, a newline+Bcc in the email field): 2 leads held, 0 injected strings or invisible characters in any draft, the build invokes only `cap.outreach.enqueue`. Charset rule: 6 unsafe names held, 4 legitimate (accented, Arabic, parentheses, `#`) allowed.
  - **revenue forged:** a revenue field is rejected by schema; `paid_unverified` leaves revenue UNKNOWN.
  - **defaults:** DenyTransport + dry run → `forced` dry result with no state change; with dry-run off, the deny transport still refuses (`transport_disabled`).
  - **scheduler:** an unattended send is refused (`risk_exceeds_unattended_ceiling`).

  Mutants: 26 new `out_*`, all killed in filtered runs (171 defined).
- **remaining gaps:**
  1. **Approval key custody.** `approval.key` sits in the state dir (0600) on the shared box. Any process running as the same user can read it and mint tokens. The approval property holds only if that key is held by the human/approver path. UNKNOWN until an approver UI or out-of-box signer exists.
  2. **The draft MAC key is in the same state dir.** A key holder can rewrite a draft, but cannot reuse an approval issued for the old hash.
  3. **Recipient validity is syntactic only.** Mailbox existence and consent are UNKNOWN. Opt-out depends on someone recording replies through the tracker; nothing reads replies.
  4. **Caps are per outbox DB.** Two state dirs mean two independent caps.
  5. **Name-only dedupe** can merge or split businesses wrongly. Phone/email keys mitigate this.
  6. **The CLI runtime starts the ControlPlane with `require_host_hardening=False`.**
  7. **`grants.sqlite`, `ops.sqlite` and `outbox.sqlite` are created 0644** inside a 0700 dir.
  8. **The real transport does not exist.** Delivery, bounces and reply ingestion are untested by design.
- **reversal criteria:** any violation or mutant regression blocks merge.

## S-0013: cycle-011 two-phase Gmail handoff: threat model, evidence, gaps
- **decision_id:** S-0013
- **date:** 2026-10-20
- **problem:** Real sends will now happen (by the user, through Gmail) after merge. The Python process cannot deliver mail, so the risk moves to the handoff boundary: sending something not planned, sending twice, sending past caps, sending after an opt-out, or sending edited content.
- **controls:** see D-0029. Every planned send passes `cap.outreach.send` (risk 3) with a per-draft token bound to draft hash, recipient, channel, `mode=handoff` and grant ref; caps and suppression are re-checked in the reservation transaction; verify-manifest re-checks each entry immediately before sending; every confirmation goes through `cap.outreach.confirm`.
- **evidence:** `HANDOFF_ADVERSARIAL cases=9 violations=0`:
  - **not_in_manifest:** an unknown/forged reservation, a reservation from another outbox, and a confirmation keyed by draft id are all refused; 0 send rows.
  - **confirm_twice:** replay → `replayed`; other message id or result → `conflict`; fresh op id → `already_sent`; reused message id → `message_id_reused`; 1 send row.
  - **cap_evasion:** same-day re-plan reserves 0 and keeps the 4 open entries; a failed draft and 3 in-doubt drafts are never re-planned; 4 recipient spellings posing as 4 leads get 1 on day 1 and 2 in total.
  - **suppressed_after_opt_out:** a `STOP` reply from a case/+tag variant suppresses the mailbox, cancels its reservation, makes verify say `suppressed`, and sets `opted_out`; a positive reply quoting our opt-out line does not suppress; a send reported anyway is recorded with `after_cancel`; the recipient is never planned again.
  - **body_edited_after_plan:** an edited body file → `body_file_changed`; an edited DB draft → `draft_changed`; an edited manifest recipient → `manifest_edited`; recording those sends flags the anomaly.
  - **grant_controls:** no grant → `grant_missing`; a standing grant cannot mint a live-mode token; a live-mode token cannot authorize a handoff; revoke between issue and execute → `grant_inactive`; `max_sends=2` → 2 reserved; a DB-raised ceiling → `grant_tampered`.
  - **key_custody:** a key inside the state dir (or below it) and a 0644 key are refused.
  - **handoff_needs_explicit_config:** default context (DenyTransport, dry run) and a non-handoff transport reserve nothing; `HandoffTransport.send` raises.
  - **opt_out_line_required:** a body without the opt-out line cannot be enqueued.
  - Mutants: 26 new `hof_*`, all killed (197 defined).
- **remaining gaps:**
  1. **Same host, same uid.** The approval key moved out of the state folder but stays on the shared box. Any process running as `box` can read it, record a grant and plan sends. Custody is separated, not isolated. An out-of-box signer is still UNKNOWN/not built.
  2. **The external send is not observed by this repo.** If the sender ignores verify-manifest or sends text other than the body file, the repo only learns what `record-sent` is told. Unreported sends are invisible to caps; reservations left unconfirmed become `in_doubt` and keep consuming caps (fail-safe).
  3. **Reply ingestion is manual.** Nothing polls Gmail. Opt-outs take effect only once `record-reply` is run. Keyword matching is English-only and heuristic; false negatives are possible (e.g. Arabic replies).
  4. **Gmail message ids** are taken on trust (format-checked only).
  5. Cycle-010 gaps 2–7 stand (MAC key in the state dir, syntactic recipients, per-DB caps, name dedupe, `require_host_hardening=False`, 0644 SQLite files).
  6. **Legacy key.** `/workspace/outreach_state/approval.key` (cycle-010) still exists; it is no longer read. Deleting it is recommended.
- **reversal criteria:** any violation or mutant regression blocks merge.
