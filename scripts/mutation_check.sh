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
  # cycle-004: killpg alone is now an equivalent mutant (the PID namespace tears the tree down
  # even if only bwrap is killed; see D-0013). Mutate both layers so the test must catch it.
  "worker_kill_pid_not_group_no_pidns|tools/subprocess_handler.py|s/os.killpg(proc.pid, signal.SIGKILL)/proc.kill()/g"
  "worker_sensitive_env_ok|tools/subprocess_handler.py|s/if SENSITIVE_ENV.search(name):/if False:/"
  "worker_skip_tool_root_check|tools/worker.py|s/if not os.path.realpath(getattr(module, \"__file__\", None) or \"\/\").startswith(root):/if False:/"
  "mcp_default_risk_read|tools/mcp_adapter.py|s/RiskLevel.IRREVERSIBLE, ann_json, error))/RiskLevel.READ, ann_json, error))/"
  "mcp_import_as_accepted|tools/registry.py|s/TrustState.CANDIDATE, f\"annotations=/TrustState.ACCEPTED, f\"annotations=/"
  # Cycle 4 (cycle-004-isolation)
  "sandbox_no_netns|tools/sandbox.py|s/\"--unshare-net\", //"
  "sandbox_no_pidns|tools/sandbox.py|s/\"--unshare-pid\", //"
  "sandbox_bind_host_root|tools/sandbox.py|s/\"--ro-bind\", \"\/usr\", \"\/usr\"\]/\"--ro-bind\", \"\/\", \"\/\"]/"
  "sandbox_tool_root_writable|tools/sandbox.py|s/\"--ro-bind\", tool_root,/\"--bind\", tool_root,/"
  "sandbox_trust_any_bwrap|tools/sandbox.py|s/and st.st_uid == 0$/or True/"
  "sandbox_probe_ignored|tools/sandbox.py|s/if r.returncode != 0:/if False:/"
  "host_hardening_optional|control_plane/executor.py|s/if require_host_hardening and not hardened:/if False:/"
  "host_hardening_not_applied|control_plane/executor.py|s/hardened = set_non_dumpable()/hardened = True/"
  "registry_not_persisted|tools/registry.py|s/version = self._store.record(/version = 0 and self._store.record(/"
  "registry_reload_skips_validation|tools/registry.py|s/self._activate(spec, WorkerSpec(\*\*w))/pass/"
  # Cycle 5 (cycle-005-browser-egress)
  "egress_skip_allowlist|tools/egress.py|s/if target not in self._allow:/if False:/"
  "egress_resolve_before_allowlist|tools/egress.py|s/if target not in self._allow:/if (_resolve(host, port) and False) or target not in self._allow:/"
  "egress_skip_rebinding_guard|tools/egress.py|s/if not literal and not ipaddress.ip_address(ip).is_global:/if False:/"
  "egress_skip_connection_budget|tools/egress.py|s/over = self._conns > self._max_conn/over = False/"
  "egress_skip_byte_budget|tools/egress.py|s/return self._bytes > self._max_bytes/return False/"
  "egress_lenient_grammar|tools/egress.py|s/LINE.fullmatch(/LINE.match(/"
  "egress_deny_not_audited|tools/egress.py|s/self._event(\"egress_denied\", reason=reason, \*\*fields)/pass/"
  "egress_allowed_not_audited|tools/egress.py|s/self._event(\"egress_allowed\", host=host, port=port, ip=ip)/pass/"
  "egress_without_audit_ok|tools/subprocess_handler.py|s/if spec.egress and audit is None:/if False:/"
  "egress_entry_validation_off|tools/subprocess_handler.py|s/if not valid_entry(entry):/if False:/"
  "egress_low_risk_ok|tools/registry.py|s/if worker.egress and spec.risk < RiskLevel.EXTERNAL:/if False:/"
  "nproc_limit_removed|tools/worker.py|s/resource.setrlimit(resource.RLIMIT_NPROC, (max_procs, max_procs))/pass/"
  # Cycle 6 (cycle-006-memory)
  "memory_capability_writes_trusted|memory/handlers.py|s/trust=\"untrusted\",  # never from args/trust=\"operator\",/"
  "memory_skip_redaction|memory/store.py|s/content, redactions = redact_text(content)/redactions = 0/"
  "memory_skip_mac_verify|memory/store.py|s/if not hmac.compare_digest(self._mac(row), row\[\"mac\"\]):/if False:/"
  "memory_skip_content_hash_verify|memory/store.py|s/if not row\[\"purged\"\] and hashlib/if False and hashlib/"
  "memory_skip_dedup|memory/store.py|s/if existing is not None:/if False:/"
  "memory_skip_record_budget|memory/store.py|s/if len(content.encode()) > self.budget.max_record_bytes:/if False:/"
  "memory_skip_store_budget|memory/store.py|s/if total + nbytes > self.budget.max_total_bytes:/if False:/"
  "memory_skip_count_budget|memory/store.py|s/if live + 1 > self.budget.max_records:/if False:/"
  "memory_ignore_expiry|memory/store.py|s/r.expires_at > ?/r.expires_at > ? - 1e12/g"
  "memory_url_secret_ok|memory/store.py|s/if contains_secret(origin_url):/if False:/"
  "memory_chain_check_off|memory/store.py|s/if row\[\"prev_mac\"\] != prev:/if False:/"
  "memory_fts_raw_query|memory/store.py|s/q = self._fts_query(query)/q = query/"
  "memory_results_unlabelled|memory/store.py|s/\"type\": \"untrusted_data\", \"trust\"/\"type\": \"data\", \"trust\"/"
  "memory_audit_anchor_off|memory/handlers.py|s/self._audit.append(\"memory_write\", /(lambda *a, **k: None)(\"memory_write\", /"
)
fail=0; killed=0; ran=0
ONLY="${MUTANT_ONLY:-}"  # optional regex for local iteration; CI runs all
for m in "${MUTANTS[@]}"; do
  IFS='|' read -r name file expr <<<"$m"
  if [ -n "$ONLY" ] && ! [[ "$name" =~ $ONLY ]]; then continue; fi
  ran=$((ran+1))
  rm -rf "$WORK/w"; mkdir "$WORK/w"; cp -r "$ROOT/src" "$ROOT/tests" "$WORK/w/"
  target="$WORK/w/src/$file"
  sed -i "$expr" "$target"
  if [ "$name" = "worker_kill_pid_not_group_no_pidns" ]; then
    sed -i 's/"--unshare-pid", //' "$WORK/w/src/tools/sandbox.py"
  fi
  if cmp -s "$target" "$ROOT/src/$file"; then
    echo "$name: NOT_APPLIED"; fail=1; continue
  fi
  if (cd "$WORK/w" && timeout 120 python3 -m unittest discover -f -s tests -t . >/dev/null 2>&1); then
    echo "$name: SURVIVED"; fail=1
  else
    echo "$name: killed"; killed=$((killed+1))
  fi
  # a mutant that breaks process-group kill leaves the fixture's grandchild alive; reap it
  for t in 3599 3598 3597 3596; do pkill -fx "sleep $t" 2>/dev/null || true; done
  pkill -f "/worker/worker.py" 2>/dev/null || true
done
echo "MUTATION mutants=$ran killed=$killed${ONLY:+ (filtered: $ONLY)}"
[ -z "$ONLY" ] && [ "$ran" -ne "${#MUTANTS[@]}" ] && fail=1
exit $fail
