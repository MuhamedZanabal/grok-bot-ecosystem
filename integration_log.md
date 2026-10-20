# Integration log (append-only)

## I-0001: Control plane integrated on `cycle-1/control-plane`
- **decision_id:** I-0001
- **date:** 2026-10-19
- **problem:** Integrate Cycle 1 components as one gate.
- **options:** separate modules called ad hoc by agents, or a single `ControlPlane.execute` pipeline.
- **evidence:** implementation commit `2784633b4300e6f005bee2b6b3401e420a9fcd55`. Pipeline order is in `src/control_plane/executor.py` (validate -> kill -> policy -> handler bound -> replay -> approval -> rate limit -> budget -> claim -> run). Local run: `python3 -m unittest discover -s tests -t .` -> `Ran 52 tests in 0.528s / OK` (Python 3.13.5); `uv run --no-project --python 3.11 python -m unittest discover -s tests -t .` -> `Ran 52 tests / OK`.
- **selected:** single pipeline. Handlers can only be bound to registered capabilities (`ControlPlane.bind`).
- **rejected:** ad hoc calls, which cannot guarantee every guard runs.
- **tradeoffs:** a replay is served before approval and budget checks, so it is free and needs no new token. Kill switch and policy still apply to replays.
- **reversal criteria:** a guard needs to run after execution (e.g. output filtering). Add a post-execution stage instead of changing the order.
- **CI result:** UNKNOWN at time of writing; appended below once the PR run finishes.

## I-0002: CI result for PR #1
- **decision_id:** I-0002
- **date:** 2026-10-19
- **problem:** Record the real CI outcome for the Cycle 1 branch.
- **options:** n/a (observation).
- **evidence:** `gh pr checks 1` -> `test (3.11) pass`, `test (3.12) pass`, `test (3.13) pass`. `gh run view 39319720689 --json headSha,conclusion` -> `{"conclusion":"success","headSha":"560de621c8ddb0a3817dac10630eb243e57f3ed3"}`. Log excerpts: `Ran 52 tests ... OK` on all three versions; `ADVERSARIAL cases=20 unauthorized_executions=0`; `MUTATION mutants=10 killed=10`.
- **selected:** success metric met for `cycle-001-control-plane` at `560de621`.
- **rejected:** n/a.
- **tradeoffs:** CI checks the PR head. This artifact-only follow-up commit gets its own run, visible on PR #1.
- **reversal criteria:** any later CI failure on this branch reopens the cycle.

## I-0003: PR #1 merged to main
- **decision_id:** I-0003
- **date:** 2026-10-19
- **problem:** Land Cycle 1.
- **options:** see D-0005.
- **evidence:** `gh pr checks 1` gave `test (3.11) pass`, `test (3.12) pass`, `test (3.13) pass` (run 39319854185). `gh pr view 1 --json state,mergeCommit` gave `{"mergeCommit":{"oid":"7edabd1e8efe6794446c9a9a41256c8088f0decc"},"state":"MERGED"}`.
- **selected:** merge commit `7edabd1`.
- **rejected:** n/a.
- **tradeoffs:** no CI-required branch protection (S-0004).
- **reversal criteria:** n/a.

## I-0004: Cycle 2 integrated on `cycle-2/orchestration`
- **decision_id:** I-0004
- **date:** 2026-10-19
- **problem:** Integrate the orchestration substrate and persistent grants.
- **options:** see D-0004 and D-0006.
- **evidence:** definition commit `8390b2d420ebc795822c1b14e087abf1e630d488` (D-0004, recorded before code); implementation commit `2c13995821cf157675e668fbeb4cf582b024cc02` (`git diff --shortstat main HEAD -- src` gave `9 files changed, 607 insertions(+), 12 deletions(-)`, within the ~800-line budget). Local results:
  - `python3 -m unittest discover -s tests -t .` gave `Ran 93 tests ... OK`.
  - `uv run --no-project --python 3.11 python -m unittest discover -s tests -t .` gave `Ran 93 tests ... OK`.
  - `ADVERSARIAL cases=20 unauthorized_executions=0`.
  - `ORCH_ADVERSARIAL cases=18 unauthorized_executions=0`.
  - `bash scripts/mutation_check.sh` gave `MUTATION mutants=20 killed=20`.
- **success criteria (D-0004):**
  1. All tests pass: met locally.
  2. 0 unauthorized executions over >= 15 model-output cases: met (18/0), and the control-plane set still gives 20/0.
  3. Duplicates execute once: met (`test_duplicate_proposals_execute_once`, `test_concurrent_duplicates_execute_once`).
  4. Timeout, outage and malformed output end in documented states: met (`test_provider_timeout_falls_back_to_next_provider`, `test_total_outage_is_provider_failed` gives `provider_failed`, `test_malformed_output_limit_aborts` gives `malformed_output`).
  5. Grants persist, are versioned and audited: met (`tests/test_grant_store.py`).
  6. All new mutants killed: met (10/10 new).
- **selected:** `PolicyEngine` always uses a `GrantStore` (in-memory SQLite by default, file-backed in tests), so there is one code path.
- **rejected:** keeping a separate in-memory grant set, which would mean two code paths.
- **tradeoffs:** the Cycle 1 `PolicyEngine()` constructor still works but is now non-durable unless given a file-backed store.
- **reversal criteria:** grant lookups dominate latency (they do not: B-0003).
- **CI result:** UNKNOWN at time of writing; appended once the PR run finishes.

## I-0005: CI result for PR #2
- **decision_id:** I-0005
- **date:** 2026-10-19
- **problem:** Record the real CI outcome for Cycle 2.
- **options:** n/a (observation).
- **evidence:** `gh pr checks 2` gave `test (3.11) pass`, `test (3.12) pass`, `test (3.13) pass`. `gh run view 39321439337 --json headSha,conclusion` gave `{"conclusion":"success","headSha":"7a1cdace4c2218f75a3f905723c3a9356d3c88db"}`. Log excerpts: `Ran 93 tests` on 3.11, 3.12 and 3.13; `ADVERSARIAL cases=20 unauthorized_executions=0`; `ORCH_ADVERSARIAL cases=18 unauthorized_executions=0`; `MUTATION mutants=20 killed=20`.
- **selected:** D-0004 success metric met at `7a1cdace` in CI.
- **rejected:** n/a.
- **tradeoffs:** this artifact-only follow-up commit gets its own run on PR #2. Merging is not CI-gated (S-0004).
- **reversal criteria:** any later CI failure on this branch reopens the cycle.

## I-0006: PR #2 merged to main
- **decision_id:** I-0006
- **date:** 2026-10-19
- **problem:** Land Cycle 2.
- **options:** merge commit pinned to head (as D-0005).
- **evidence:** `gh pr checks 2` gave all three `pass` (run 39321650052). `gh pr merge 2 --merge --match-head-commit 6f24f4a835b8341cb1eb3932999756340fc39a54` then `gh pr view 2 --json state,mergeCommit` gave `{"mergeCommit":{"oid":"27ac19430bbe25651cac2383d3063d8dbf0a297b"},"state":"MERGED"}`.
- **selected:** merge commit `27ac194`.
- **rejected:** n/a.
- **tradeoffs:** still no CI-required branch protection (S-0004).
- **reversal criteria:** n/a.

## I-0007: Cycle 3 integrated on `cycle-3/tool-substrate`
- **decision_id:** I-0007
- **date:** 2026-10-19
- **problem:** Integrate out-of-process tools, schemas, the registry and the MCP adapter.
- **options:** see D-0008 to D-0011.
- **evidence:** definition commit `11238453e3abb8d14b1be9d73554b5b694c25afb` (D-0008, before code); implementation commit `dbf97a531e207e9721d609ad6fd10486dcc563a4` (`git diff --shortstat main HEAD -- src` gave `12 files changed, 678 insertions(+), 5 deletions(-)`, within the ~900-line budget). Local results:
  - `python3 -m unittest discover -s tests -t .` gave `OK`.
  - `uv run --no-project --python 3.11 python -m unittest discover -s tests -t .` gave `Ran 135 tests ... OK`.
  - `TOOL_ADVERSARIAL cases=20 unauthorized_executions=0`; prior sets still `20/0` and `18/0`.
  - `bash scripts/mutation_check.sh` gave `MUTATION mutants=32 killed=32`.
  - The process check after the normal suite, `pgrep -c -f 'sleep 60'`, gave `0`.
- **success criteria (D-0008):**
  1. Tests pass: met locally.
  2. Worker and child dead after timeout: met (`test_timeout_kills_worker_and_its_children`; mutant `worker_kill_pid_not_group` killed).
  3. Canary absent, no inherited fds: met (`test_env_is_allowlist_only_and_canary_absent`, `test_no_parent_file_descriptors_inherited`).
  4. Tool code cannot import or find control-plane objects, gap test flipped: met (`test_gap_closed_tool_code_cannot_reach_handlers`).
  5. 0 unauthorized over >= 12 new cases: met (20/0).
  6. All new mutants killed: met (12/12).
- **selected:** integrated as above.
- **rejected:** n/a.
- **tradeoffs:** a strict control plane is now the default; existing in-process callers must opt in explicitly.
- **reversal criteria:** see D-0009.
- **CI result:** UNKNOWN at time of writing; appended once the PR run finishes.

## I-0008: First CI run on PR #3 failed (leak check). Root cause and fix
- **decision_id:** I-0008
- **date:** 2026-10-19
- **problem:** Run 39323831631 on `42b7f9a` gave `test (3.11) pass`, `test (3.12) pass`, `test (3.13) fail`. The failing step was "No leaked worker processes" (`pgrep -f "sleep 60"` gave PIDs `7904 7920`, then `leaked worker children`).
- **options:** treat it as a product leak; or as a CI-ordering artifact.
- **evidence:**
  - In that same 3.13 job every functional step passed: `Ran 135 tests`, `TOOL_ADVERSARIAL cases=20 unauthorized_executions=0`, `MUTATION mutants=32 killed=32`.
  - The leak step ran *after* the mutation check, and on 3.11/3.12, where the mutation check does not run, the same leak step passed.
  - Mutant `worker_kill_pid_not_group` deliberately breaks the process-group kill, so it leaves the fixture's grandchild alive. Exactly two such processes were seen locally after a mutation run (one per timeout test).
  - After the normal suite alone, `pgrep -c -f 'sleep 60'` gave `0` locally.
  - So the failure was the check's ordering, not a product leak.
- **selected:** fixture child is now a distinctive `sleep 3599`. The leak check runs *before* the mutation check and matches exactly (`pgrep -fx "sleep 3599"`). `scripts/mutation_check.sh` reaps that exact process after each mutant. Local verification: the full mutation check gave `MUTATION mutants=32 killed=32` and then `strays after mutation: 0`; the normal suite then gave `no leaked worker processes`.
- **rejected:** deleting the leak check, which would lose real leak detection.
- **tradeoffs:** none.
- **reversal criteria:** the leak check fails with the mutation check excluded, which would indicate a real product leak.

## I-0009: CI result for PR #3 after the fix
- **decision_id:** I-0009
- **date:** 2026-10-19
- **problem:** Record the real CI outcome for Cycle 3.
- **options:** n/a (observation).
- **evidence:** `gh pr checks 3` gave `test (3.11) pass`, `test (3.12) pass`, `test (3.13) pass`. `gh run view 39325010443 --json headSha,conclusion` gave `{"conclusion":"success","headSha":"12cefdd578ad96efd76cf9f58f007a5d9cac3d01"}`. Log excerpts: `Ran 135 tests` on all three; `TOOL_ADVERSARIAL cases=20 unauthorized_executions=0`; `no leaked worker processes` on all three; `MUTATION mutants=32 killed=32`; `HARDENING ... hardened={'hardened': True, 'readable': False, 'canary': False}` (from run 39323831631, same tests).
- **selected:** D-0008 success metric met in CI at `12cefdd`.
- **rejected:** n/a.
- **tradeoffs:** this artifact-only follow-up commit gets its own run on PR #3.
- **reversal criteria:** any later CI failure on this branch reopens the cycle.

## I-0010: PR #3 (Cycle 3) merged
- **decision_id:** I-0010
- **date:** 2026-10-20
- **problem:** Merge Cycle 3 after a green CI.
- **options:** n/a.
- **evidence:**
  - Before merging, `gh pr checks 3` gave `test (3.11) pass`, `test (3.12) pass`, `test (3.13) pass` (run 39325609307 on head `a4554490`).
  - Merged with a merge commit pinned to head SHA `a4554490f7cd61d7580715bdf7a68b4847cb2e1b`.
  - `gh pr view 3 --json state,mergeCommit,headRefOid,mergedAt` gave `MERGED`, `mergeCommit 12f0b7fae2664eb2cefec2c5a159e13d7b75fa4e`, `mergedAt 2026-10-19T21:18:14Z` (2026-10-20 00:18 UTC+3).
- **selected:** main = `12f0b7f`, the baseline for cycle-004-isolation.
- **rejected:** n/a.
- **tradeoffs:** branch protection is still unavailable (S-0004), so the check was procedural.
- **reversal criteria:** n/a.

## I-0011: cycle-004-isolation integrated on branch `cycle-4/isolation`
- **decision_id:** I-0011
- **date:** 2026-10-20
- **problem:** Integrate D-0012 scope (a)-(d).
- **options:** see D-0013, D-0014, R-0009.
- **evidence:**
  - Added `src/tools/sandbox.py` (130 lines) and `src/tools/registry_store.py` (80 lines). `src/tools/hardening.py` moved to `src/control_plane/hardening.py`, with a re-export left behind. Edited `executor.py`, `registry.py`, `subprocess_handler.py` and `__init__`s. Production diff is about 310 lines, within the ≤600 budget.
  - Local results:
    - 3.13.5, 3.11 (uv) and 3.12 (uv): `Ran 146 tests ... OK`.
    - `ADVERSARIAL cases=20 unauthorized_executions=0`; `ORCH_ADVERSARIAL cases=18 unauthorized_executions=0`; `TOOL_ADVERSARIAL cases=20 unauthorized_executions=0`; `SANDBOX_ADVERSARIAL cases=21 escapes=0`.
    - `pgrep -fx "sleep 35(99|98|97|96)"` after the handler and sandbox suites gave none (`no-leaks`).
    - `bash scripts/mutation_check.sh` gave `MUTATION mutants=42 killed=42` (exit 0); afterwards `pgrep -fa "sleep 35"` gave none.
  - Pinned gap tests flipped:
    - `test_residual_same_uid_proc_environ` → `test_gap_closed_parent_proc_invisible`.
    - The Cycle 3 unhardened baseline (`canary: True`) → `test_gap_closed_sandbox_hides_host_environ_even_if_unhardened` (`SANDBOX_PROC unhardened_host={'ok': False, 'visible_pids': 2, 'host_hardened': False}`).
    - Timeout tests now verify host-side that `sleep 3599` is gone.
    - `test_residual_trusted_inprocess_code_can_invoke_launchers` stays pinned (S-0006 gap).
  - `tests/test_tool_adversarial.py` now counts executions host-side (Popen of `/worker/worker.py`), because the sandboxed tool can no longer write a host marker file. That is itself evidence of scope (a).
- **selected:** integrate.
- **rejected:** n/a.
- **tradeoffs:** see D-0013 and D-0014.
- **reversal criteria:** any CI failure, or any escape.
- **CI result:** appended as I-0012 once the PR run finishes.

## I-0012: CI result for PR #4 (cycle-004-isolation)
- **decision_id:** I-0012
- **date:** 2026-10-20
- **problem:** Record the real CI outcome, including the previously UNKNOWN runner sandbox support.
- **options:** n/a (observation).
- **evidence:**
  - `gh pr checks 4` gave `test (3.11) pass 35s`, `test (3.12) pass 35s`, `test (3.13) pass 7m34s`. `gh run view 39329642600 --json headSha,conclusion` gave `{"conclusion":"success","headSha":"f0749ca0cd76068d5fc052f165636c919486b90f"}`.
  - **Runner sandbox facts (formerly UNKNOWN):**
    - bubblewrap is not preinstalled; apt installed `bubblewrap 0.9.0`.
    - `kernel.apparmor_restrict_unprivileged_userns = 1` by default, and the bwrap probe was blocked, giving `SANDBOX_CI_PROBE=blocked_relaxing_apparmor_userns_restriction`.
    - After the CI-only `sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` the probe passed, and `SANDBOX Sandbox(bwrap='/usr/bin/bwrap', python='/opt/hostedtoolcache/Python/3.1x.y/x64/bin/python3.1x')` was detected on all three jobs.
  - All three jobs: `Ran 146 tests` OK, no skips; `ADVERSARIAL cases=20 unauthorized_executions=0`; `ORCH_ADVERSARIAL cases=18 unauthorized_executions=0`; `TOOL_ADVERSARIAL cases=20 unauthorized_executions=0`; `SANDBOX_ADVERSARIAL cases=21 escapes=0`; `SANDBOX_PROC unhardened_host={'ok': False, 'visible_pids': 2, 'host_hardened': False}`; `HARDENING control_plane_construction={'before': False, 'after': True}`; `no leaked worker processes`.
  - 3.13 job: `MUTATION mutants=42 killed=42`.
- **selected:** the D-0012 success metric is met in CI at `f0749ca`.
- **rejected:** n/a.
- **tradeoffs:** CI passes only with the AppArmor userns restriction relaxed (D-0013). Default Ubuntu 24.04 hosts would fail closed until configured.
- **reversal criteria:** any later CI failure on this branch reopens the cycle.

## I-0013: PR #4 (cycle-004-isolation) merged
- **decision_id:** I-0013
- **date:** 2026-10-20
- **problem:** Merge cycle-004 after a green CI.
- **options:** n/a.
- **evidence:**
  - `gh pr checks 4` gave `test (3.11) pass`, `test (3.12) pass`, `test (3.13) pass` (run 39330724213 on head `60b0a11`).
  - `gh pr merge 4 --merge --match-head-commit 60b0a11bbaee185a75296555173c7c533f2d754a`.
  - `gh pr view 4` gave `MERGED`, `mergeCommit 58fc1ca7f1408e2d92972374a143b7b21d38b3f7`, `mergedAt 2026-10-19T22:01:56Z` (2026-10-20 01:01 UTC+3).
- **selected:** main = `58fc1ca`, the baseline for cycle-005.
- **rejected:** n/a.
- **tradeoffs:** no branch protection (S-0004).
- **reversal criteria:** n/a.

## I-0014: cycle-005-browser-egress integrated on branch `cycle-5/browser-egress`
- **decision_id:** I-0014
- **date:** 2026-10-20
- **problem:** Integrate D-0015 scope: egress, fork limit, browser fetch tier.
- **options:** D-0015, D-0017, R-0011, R-0012.
- **evidence:**
  - **New code:**
    - `src/tools/egress.py` (229 lines), `src/builtin_tools/browser.py` (about 170), `src/builtin_tools/egress_client.py` (34), `src/tools/builtins.py` (36).
    - Edited `subprocess_handler.py` (WorkerSpec `egress`, `max_procs` and budgets; the proxy lifecycle per call; egress requires audit), `registry.py` (egress requires risk ≥ EXTERNAL; handler gets the audit sink and capability id), `sandbox.py` (`extra_ro_binds`) and `worker.py` (`RLIMIT_NPROC`).
    - About 550 new production lines, within the ≤700 budget. No new dependency.
  - **Local runs:** `python3 -m unittest discover -s tests -t .` gave `Ran 153 tests ... OK` (3.13.5). 3.11 and 3.12 via uv also gave OK after the D-0017 CA fix.
  - **Adversarial output:**
    - `EGRESS_ADVERSARIAL cases=26 unauthorized_egress=0 proxy_attempts=22 audited_attempts=22 audit_chain_ok=True resolutions=['localhost']`. The only resolution was the allowlisted `localhost` in the rebinding-guard case; the canary and `*.invalid` names were never resolved.
    - `FORK_BOMB fork_count={'errno': 11, 'forked': 30} bomb_peak_procs=15 timed_out=True left=0`.
    - `ADVERSARIAL 20/0`, `ORCH_ADVERSARIAL 18/0`, `TOOL_ADVERSARIAL 20/0` and `SANDBOX_ADVERSARIAL 21/0` are unchanged.
  - **Browser:** `BROWSER_FETCH {"ok": true, "status": 200, "title": "Fixture Page", "links": ["http://127.0.0.1:<port>/next", "http://other.invalid/z"]}` via registry → policy → approval → sandbox → proxy → local fixture. Without an approval it is `approval_required` (fixture hits `[]`). A `file:` URL is `rejected` by the schema. An ungranted actor is `denied`.
  - **Mutation:**
    - The first full run (without failfast) gave `MUTATION mutants=54 killed=54`, `exit=0`, in about 30 min locally.
    - `unittest -f` (failfast) was then added to the mutation script for speed. A subset of 13 gave `13/13` in 96 s. The full failfast run gave `MUTATION mutants=54 killed=54`, `exit=0`, `elapsed=453s`.
  - **Cleanup:** after the suites, `pgrep -fa "worker.py|sleep 35"` gave none. Fixture servers are shut down in `tearDown`; proxies are stopped in the handler's `finally`.
- **selected:** integrate.
- **rejected:** n/a.
- **tradeoffs:** see S-0007.
- **reversal criteria:** any CI failure or unauthorized egress.

## I-0015: CI result for PR #5 (cycle-005-browser-egress)
- **decision_id:** I-0015
- **date:** 2026-10-20
- **problem:** Record the real CI outcome.
- **options:** n/a (observation).
- **evidence:**
  - `gh pr checks 5` gave `test (3.11) pass 46s`, `test (3.12) pass 48s`, `test (3.13) pass 9m10s`. `gh run view 39338745621 --json headSha,conclusion` gave `{"conclusion":"success","headSha":"b8c7a4b2d4ce91afc85d8226436c735a3ec957d0"}`.
  - All three jobs:
    - `Ran 153 tests` OK.
    - `EGRESS_ADVERSARIAL cases=26 unauthorized_egress=0 proxy_attempts=22 audited_attempts=22 audit_chain_ok=True resolutions=['localhost']`.
    - `FORK_BOMB fork_count={'errno': 11, 'forked': 30} bomb_peak_procs=15 timed_out=True left=0`. That means `RLIMIT_NPROC` is per-userns on the runner kernel too. The kernel version itself was not printed and is UNKNOWN.
    - `BROWSER_FETCH {"ok": true, "status": 200, "title": "Fixture Page", ...}`.
    - `SANDBOX_ADVERSARIAL 21/0`, `TOOL_ADVERSARIAL 20/0`, `ORCH_ADVERSARIAL 18/0`, `ADVERSARIAL 20/0`.
    - `SANDBOX_CI_PROBE=blocked_relaxing_apparmor_userns_restriction` (as I-0012).
    - The leak step printed `no leaked worker processes`. The string `leaked worker children` in the log is only the echoed script source (log line 451), not output.
  - 3.13 job: `MUTATION mutants=54 killed=54`.
- **selected:** the D-0015 success metric is met in CI at `b8c7a4b`.
- **rejected:** n/a.
- **tradeoffs:** CI still depends on the AppArmor relaxation (D-0013).
- **reversal criteria:** any later CI failure on this branch reopens the cycle.

## I-0016: PR #5 (cycle-005) merged
- **decision_id:** I-0016
- **date:** 2026-10-20
- **problem:** Merge cycle-005 after a green CI.
- **options:** n/a.
- **evidence:**
  - `gh pr checks 5` gave `test (3.11) pass`, `test (3.12) pass`, `test (3.13) pass` (run 39339865437 on head `8ca78c2`).
  - `gh pr merge 5 --merge --match-head-commit 8ca78c21420b55258cfdf81b8644f3d2a0d126f1`.
  - `gh pr view 5` gave `MERGED`, `mergeCommit 8e30c65c430d36281a5b8ba8e4cf4cedee1ddfb9`, `mergedAt 2026-10-19T23:20:30Z` (2026-10-20 02:20 UTC+3).
- **selected:** main = `8e30c65`, the baseline for cycle-006.
- **rejected:** n/a.
- **tradeoffs:** no branch protection (S-0004).
- **reversal criteria:** n/a.

## I-0017: cycle-006-memory integrated on branch `cycle-6/memory`
- **decision_id:** I-0017
- **date:** 2026-10-20
- **problem:** Integrate D-0018 scope.
- **options:** D-0018, D-0019, R-0013.
- **evidence:**
  - **New code:** `src/memory/{store,handlers,ingest,__init__}.py` (386 lines). `control_plane` gains `TrustedHostHandler`, `audit.redact_text` and `contains_secret` (+27 lines). About 413 production lines, within the ≤600 budget. Stdlib only.
  - **Tests:** `python3 -m unittest discover -s tests -t .` gave `Ran 161 tests ... OK` on 3.13.5, uv 3.11 and uv 3.12; `MEMORY_SEARCH_MODE=fts5`.
  - **Memory adversarial output:** `MEMORY_ADVERSARIAL cases=15 violations=0`; `POISONED_MEMORY ... dangerous_executions=0 trusted_records=0 state=completed`.
  - **Browser wiring:** `MEMORY_FROM_FETCH {"origin_url": "http://127.0.0.1:<port>/page?q=fox&n=2", "source_id": "cap.browser.fetch", "source_ref": "op-fetch-mem-01", ...}`; re-ingesting gives `replayed`.
  - **Prior sets:** 20/0, 18/0, 20/0, 21/0 and 26/0 are unchanged.
  - **Mutation:** `bash scripts/mutation_check.sh` gave `MUTATION mutants=68 killed=68`, `exit=0`, `elapsed=620s`.
  - **Cleanup:** `pgrep -fa "worker.py|sleep 35|mutation_check"` gave none.
- **selected:** integrate.
- **rejected:** n/a.
- **tradeoffs:** S-0008.
- **reversal criteria:** any CI failure or violation.

## I-0018: cycle-006 CI result on PR #6
- **decision_id:** I-0018
- **date:** 2026-10-20
- **problem:** Record the real CI result for cycle-006 (D-0018 required this).
- **options:** n/a.
- **evidence:** `gh pr checks 6` gave `test (3.11) pass 56s`, `test (3.12) pass 50s` and `test (3.13) pass 12m12s`. Run 39342990292 ran on headSha `9dd159aa67ac4138da5dcb266c228f3d0daa1d60` with conclusion `success`, from 2026-10-20 02:40 to 02:52 UTC+3. `gh run view --job <id> --log` gave the following.
  - **All three Pythons (3.11.17, 3.12.15, 3.13.16):**
    - `Ran 161 tests ... OK`
    - `MEMORY_SEARCH_MODE=fts5 fts5_available=True`
    - `MEMORY_ADVERSARIAL cases=15 violations=0`
    - `POISONED_MEMORY proposed=5 beyond_policy=5 statuses=['denied', 'denied', 'denied', 'rejected', 'denied'] dangerous_executions=0 trusted_records=0 state=completed`
    - `EGRESS_ADVERSARIAL cases=26 unauthorized_egress=0 proxy_attempts=22 audited_attempts=22 audit_chain_ok=True`
    - `FORK_BOMB ... left=0`
    - SANDBOX 21/0, TOOL 20/0, ORCH 18/0
    - `no leaked worker processes`
  - **Python 3.13 only:**
    - `MUTATION mutants=68 killed=68`
    - `BENCH memory_write n=200 p50_ms=3.350 p95_ms=4.434 mode=fts5`
    - `BENCH memory_search n=200 p50_ms=2.066 p95_ms=2.865`
    - `BENCH execute p50_ms=1.781`
    - `BENCH browser_fetch p50_ms=58.464`
    - `BENCH tool_execute_subprocess p50_ms=36.874`
- **selected:** cycle-006 metric met in CI; PR #6 left open (not merged, per spec).
- **rejected:** merging in-cycle.
- **tradeoffs:** S-0008 gaps stand. The runner kernel version is still UNKNOWN.
- **reversal criteria:** any later red run on this branch.

## I-0019: PR #6 merged
- **decision_id:** I-0019
- **date:** 2026-10-20
- **problem:** Merge cycle-006 after a green CI.
- **options:** n/a.
- **evidence:**
  - `gh pr checks 6` gave `test (3.11) pass`, `test (3.12) pass`, `test (3.13) pass` (run 39344323494 on head `a0c6277`).
  - `gh pr merge 6 --merge --match-head-commit a0c6277d1ffcd8f7af4e79c2718e14cfe93b9cbb`.
  - `gh pr view 6` gave `MERGED`, `mergeCommit d7b405b506588fc95c641b364c41a338cf25304b`, `mergedAt 2026-10-20T00:06:43Z` (03:06 UTC+3).
- **selected:** main = `d7b405b`, the baseline for cycle-007.
- **rejected:** n/a.
- **tradeoffs:** no branch protection (S-0004).
- **reversal criteria:** n/a.

## I-0020: cycle-007-research integrated on branch `cycle-7/research`
- **decision_id:** I-0020
- **date:** 2026-10-20
- **problem:** Integrate D-0020 scope.
- **options:** D-0020, D-0021, R-0014.
- **evidence:**
  - **New code:** `src/research/{sources,ledger,verifier,extract,handlers,run,__init__}.py` (785 physical lines including docstrings), plus `cap.memory.get` in `src/memory` (+28 lines). That is about 813 lines, **over the ~700 soft budget in D-0020 by about 16%** (recorded, not hidden). Stdlib only.
  - **Tests:** `python3 -m unittest discover -s tests -t .` gave `Ran 174 tests ... OK` on 3.13.5, uv 3.11 and uv 3.12, with no skips.
  - **Research output:**
    - `RESEARCH_ADVERSARIAL cases=16 violations=0`
    - `BRANCH_CLOSED passes=5 stale=3 metric=p50_ms best=30.0 reason=no_superior_candidate_in_3_consecutive_passes refused_pass_fetches=0`
    - `RESEARCH_REPORT ... claims=15 unknown=4 citations=15 identical_runs=2`
  - **Determinism scope:** the report sha is identical across two fresh harnesses on the same fixture. It differs between processes because the fixture's ephemeral port is part of each source URL, i.e. an input.
  - **Prior sets:** 20/0, 18/0, 20/0, 21/0, 26/0 and memory 15/0 are unchanged; `POISONED_MEMORY ... dangerous_executions=0`.
  - **Mutation:** `bash scripts/mutation_check.sh` gave `MUTATION mutants=94 killed=94`, `exit=0`, `real 18m45.869s`. Before the full run, every new mutant was compile-checked and its diff applied. One expression (`research_skip_quote_check`) initially also deleted the `:` and produced a syntax-error "kill"; it was fixed and re-killed on a real test failure.
  - **Cleanup:** `pgrep -fa "worker.py|sleep 35|mutation_check"` gave none.
- **selected:** integrate.
- **rejected:** n/a.
- **tradeoffs:** S-0009.
- **reversal criteria:** any CI failure or violation.
