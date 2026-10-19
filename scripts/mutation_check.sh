#!/usr/bin/env bash
# Mutation check: disable one safety guard at a time; the test suite must FAIL for each.
# Exit 0 only if every mutant is applied and killed.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
MUTANTS=(
  "skip_policy|executor.py|s/if not decision.allowed:/if False:/"
  "skip_killswitch|executor.py|s/if self.kill.engaged():/if False:/"
  "skip_approval|executor.py|s/if decision.requires_approval:/if False:/"
  "skip_replay|executor.py|s/if prior is not None:/if False:/"
  "skip_args_hash|approvals.py|s/if not hmac.compare_digest(str(claims.get(\"args_sha256\")), args_sha256(args)):/if False:/"
  "skip_expiry|approvals.py|s/if self._clock() >= claims.get(\"expires_at\", 0):/if False:/"
  "skip_scope|approvals.py|s/claims.get(\"operation_id\") != operation_id)/False)/"
  "skip_cost|governor.py|s/if self.spent + cost > self.budget.max_cost:/if False:/"
  "skip_redact|audit.py|s/\"data\": redact(fields)/\"data\": fields/"
  "skip_validation|executor.py|s/err = self._validate(request)/err = None/"
)
fail=0; killed=0
for m in "${MUTANTS[@]}"; do
  IFS='|' read -r name file expr <<<"$m"
  rm -rf "$WORK/w"; mkdir "$WORK/w"; cp -r "$ROOT/src" "$ROOT/tests" "$WORK/w/"
  target="$WORK/w/src/control_plane/$file"
  sed -i "$expr" "$target"
  if cmp -s "$target" "$ROOT/src/control_plane/$file"; then
    echo "$name: NOT_APPLIED"; fail=1; continue
  fi
  if (cd "$WORK/w" && python3 -m unittest discover -s tests -t . >/dev/null 2>&1); then
    echo "$name: SURVIVED"; fail=1
  else
    echo "$name: killed"; killed=$((killed+1))
  fi
done
echo "MUTATION mutants=${#MUTANTS[@]} killed=$killed"
exit $fail
