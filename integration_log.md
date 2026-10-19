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
