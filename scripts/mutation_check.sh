#!/usr/bin/env bash
# Mutation check: disable one safety guard at a time; the test suite must FAIL for each.
# Exit 0 only if every mutant is applied and killed.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
MUTANTS=(
  "skip_policy|control_plane/executor.py|s/if not decision.allowed:/if False:/"
  "skip_killswitch|control_plane/executor.py|s/if self.kill.engaged():/if False:/"
  "skip_approval|control_plane/executor.py|s/if decision.requires_approval:/if False:/"
  "skip_replay|control_plane/executor.py|s/if prior is not None:/if False:/"
  "skip_args_hash|control_plane/approvals.py|s/if not hmac.compare_digest(str(claims.get(\"args_sha256\")), args_sha256(args)):/if False:/"
  "skip_expiry|control_plane/approvals.py|s/if self._clock() >= claims.get(\"expires_at\", 0):/if False:/"
  "skip_scope|control_plane/approvals.py|s/claims.get(\"operation_id\") != operation_id)/False)/"
  "skip_cost|control_plane/governor.py|s/if self.spent + cost > self.budget.max_cost:/if False:/"
  "skip_redact|control_plane/audit.py|s/\"data\": redact(fields)/\"data\": fields/"
  "skip_validation|control_plane/executor.py|s/err = self._validate(request)/err = None/"
  # Cycle 2
  "skip_grant_risk_check|control_plane/policy.py|s/if granted_risk != int(cap.risk):/if False:/"
  "skip_grant_audit|control_plane/policy.py|s/if self._audit is not None:/if False:/"
  "skip_revoke_delete|control_plane/grant_store.py|s/self._db.execute(\"DELETE FROM grants WHERE actor=? AND capability=?\", (actor, capability))/pass/"
  "skip_parser_unknown_fields|orchestrator/proposal.py|s/if not _ACTION_REQUIRED <= keys or not keys <= _ACTION_ALLOWED:/if not _ACTION_REQUIRED <= keys:/"
  "skip_parser_duplicate_keys|orchestrator/proposal.py|s/raise ProposalError(\"duplicate_key\")/pass/"
  "skip_malformed_limit|orchestrator/runner.py|s/if malformed > cfg.max_malformed:/if False:/"
  "skip_blocked_limit|orchestrator/runner.py|s/if blocked > cfg.max_blocked:/if False:/"
  "skip_provider_timeout|orchestrator/runner.py|s/future.result(timeout=self._cfg.provider_timeout_s)/future.result()/"
  "skip_run_budget|orchestrator/runner.py|s/if state is not LoopState.RUNNING:/if False:/"
  "random_operation_id|orchestrator/runner.py|s/{digest\[:32\]}/{__import__(\"os\").urandom(8).hex()}/"
)
fail=0; killed=0
for m in "${MUTANTS[@]}"; do
  IFS='|' read -r name file expr <<<"$m"
  rm -rf "$WORK/w"; mkdir "$WORK/w"; cp -r "$ROOT/src" "$ROOT/tests" "$WORK/w/"
  target="$WORK/w/src/$file"
  sed -i "$expr" "$target"
  if cmp -s "$target" "$ROOT/src/$file"; then
    echo "$name: NOT_APPLIED"; fail=1; continue
  fi
  if (cd "$WORK/w" && timeout 120 python3 -m unittest discover -s tests -t . >/dev/null 2>&1); then
    echo "$name: SURVIVED"; fail=1
  else
    echo "$name: killed"; killed=$((killed+1))
  fi
done
echo "MUTATION mutants=${#MUTANTS[@]} killed=$killed"
exit $fail
