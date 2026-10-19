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
  # Cycle 3
  "skip_schema_enforcement|control_plane/executor.py|s/validate_args(self.policy.capability(cap).schema, args)/pass/"
  "inprocess_allowed_by_default|control_plane/executor.py|s/allow_inprocess_handlers: bool = False/allow_inprocess_handlers: bool = True/"
  "schema_additional_default_true|control_plane/schema.py|s/if not schema.get(\"additionalProperties\", False):/if not schema.get(\"additionalProperties\", True):/"
  "schema_pattern_search|control_plane/schema.py|s/re.fullmatch(schema\[\"pattern\"\], value)/re.search(schema[\"pattern\"], value)/"
  "schema_bool_is_int|control_plane/schema.py|s/\"integer\": isinstance(v, int) and not isinstance(v, bool),/\"integer\": isinstance(v, int),/"
  "schema_unknown_keywords_ok|control_plane/schema.py|s/unknown = set(schema) - KEYWORDS/unknown = set()/"
  "worker_env_inherit|tools/subprocess_handler.py|s/env = dict(BASE_ENV)/env = dict(os.environ)/"
  "worker_kill_pid_not_group|tools/subprocess_handler.py|s/os.killpg(proc.pid, signal.SIGKILL)/proc.kill()/g"
  "worker_sensitive_env_ok|tools/subprocess_handler.py|s/if SENSITIVE_ENV.search(name):/if False:/"
  "worker_skip_tool_root_check|tools/worker.py|s/if not os.path.realpath(getattr(module, \"__file__\", None) or \"\/\").startswith(root):/if False:/"
  "mcp_default_risk_read|tools/mcp_adapter.py|s/RiskLevel.IRREVERSIBLE, ann_json, error))/RiskLevel.READ, ann_json, error))/"
  "mcp_import_as_accepted|tools/registry.py|s/TrustState.CANDIDATE, f\"annotations=/TrustState.ACCEPTED, f\"annotations=/"
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
  # a mutant that breaks process-group kill leaves the fixture's grandchild alive; reap it
  pkill -fx "sleep 3599" 2>/dev/null || true
done
echo "MUTATION mutants=${#MUTANTS[@]} killed=$killed"
exit $fail
