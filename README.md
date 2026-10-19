# grok-bot-ecosystem

Governed agent ecosystem, built in bounded, evidence-verified cycles.

> Intelligence proposes. Policy authorizes. Deterministic infrastructure executes. Evidence verifies.

## Cycle 1: control plane (`src/control_plane/`, Python 3.11+, stdlib only)

Every action goes through `ControlPlane.execute(request, cost)`, which returns exactly one
deterministic `Status`:

| Step | Component | Blocks with |
|---|---|---|
| 1 | strict request schema (`executor.py`) | `rejected` |
| 2 | global kill switch, file-backed, fails closed (`killswitch.py`) | `killed` |
| 3 | capability policy, default DENY, explicit per-actor grants (`policy.py`) | `denied` |
| 4 | idempotency ledger, SQLite (`ledger.py`) | `replayed` / `conflict` / `in_doubt` |
| 5 | approval token for risk >= 2, HMAC-bound to actor + capability + operation_id + SHA-256(args) + expiry (`approvals.py`) | `approval_required` / `approval_invalid` |
| 6 | token-bucket rate limit (`ratelimit.py`) | `rate_limited` |
| 7 | bounded-loop governor: iteration, wall-clock and cost budgets (`governor.py`) | `cycle_aborted` / `killed` |
| 8 | handler run with timeout | `executed` / `failed` / `timed_out` |

Every decision is appended to a hash-chained JSONL audit log with secret redaction (`audit.py`).

Risk levels: 0 read, 1 reversible local write, 2 external/consequential, 3 financial/irreversible.

```bash
python3 -m unittest discover -s tests -t . -v     # invariant + adversarial tests
bash scripts/mutation_check.sh                     # each disabled guard must fail the suite
python3 bench/bench_control_plane.py 200           # execute() overhead
PYTHONPATH=src python3 -m control_plane kill engage .state/KILL --reason "operator stop"
PYTHONPATH=src python3 -m control_plane audit-verify .state/audit.jsonl
```

Governance artifacts: `ecosystem_manifest.json`, `capability_graph.json`, `decision_log.md`,
`rejection_log.md`, `integration_log.md`, `benchmark_log.md`, `security_log.md`, `roadmap.md`.
