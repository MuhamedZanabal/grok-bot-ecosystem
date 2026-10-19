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
