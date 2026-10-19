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
