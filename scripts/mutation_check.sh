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
  # Cycle 7 (cycle-007-research)
  "research_skip_hash_check|research/verifier.py|s/if row\[\"content_sha256\"\] != ptr.get(\"content_sha256\"):/if False:/"
  "research_skip_origin_match|research/verifier.py|s/if row\[\"origin_url\"\] != src\[\"url\"\]:/if False:/"
  "research_skip_quote_check|research/verifier.py|s/ or quote not in row\[\"content\"\]//"
  "research_skip_support_check|research/verifier.py|s/if not (_token(claim\[\"attribute\"\], quote) and _token(claim\[\"value\"\], quote)):/if False:/"
  "research_skip_subject_check|research/verifier.py|s/if not _token(claim\[\"subject\"\], row\[\"content\"\]):/if False:/"
  "research_skip_reserved|research/verifier.py|s/if claim.get(\"attribute\") in RESERVED:/if False:/"
  "research_allow_empty_evidence|research/verifier.py|s/if not evidence:/if False:/"
  "research_contradiction_off|research/verifier.py|s/if peer\[\"value\"\] != claim\[\"value\"\] and/if False and/"
  "research_staleness_off|research/verifier.py|s/if self.clock() - row\[\"created_at\"\] > self.max_age_s:/if False:/"
  "research_expired_ok|research/verifier.py|s/return None, \[\"stale\"\]/return None, []/"
  "research_rejected_source_ok|research/verifier.py|s/if src\[\"status\"\] == \"rejected\":/if False:/"
  "research_registry_no_evidence_ok|research/sources.py|s/if not evidence:/if False:/"
  "research_registry_illegal_ok|research/sources.py|s/if (row\[\"status\"\], to) not in LEGAL:/if False:/"
  "research_url_normalised|research/sources.py|s/hashlib.sha256(url.encode())/hashlib.sha256(url.lower().rstrip(\"\/\").encode())/"
  "research_handler_illegal_ok|research/handlers.py|s/legal = (src\[\"status\"\], args\[\"to\"\]) in LEGAL/legal = True/"
  "research_transition_skip_origin|research/handlers.py|s/if row\[\"origin_url\"\] != src\[\"url\"\]:/if False:/"
  "research_transition_skip_hash|research/handlers.py|s/if row\[\"content_sha256\"\] != ev.get(\"content_sha256\"):/if False:/"
  "research_accept_without_verified_claim|research/handlers.py|s/if to == \"accepted\" and not verified_claim:/if False:/"
  "research_evaluating_without_record|research/handlers.py|s/if to in (\"evaluating\",) and not any/if False and not any/"
  "research_transition_unaudited|research/handlers.py|s/c.audit.append(\"source_transition\", /(lambda *a, **k: None)(\"source_transition\", /"
  "research_branch_never_closes|research/ledger.py|s/CLOSE_AFTER = 3 /CLOSE_AFTER = 4 /"
  "research_superior_wrong_direction|research/ledger.py|s/return value < incumbent - min_delta if/return value > incumbent - min_delta if/"
  "research_min_delta_ignored|research/ledger.py|s/value < incumbent - min_delta/value < incumbent/"
  "research_closed_branch_runs|research/run.py|s/if b\[\"status\"\] != \"open\":/if False:/"
  "research_report_drops_citations|research/run.py|s/refs.append(cite_index\[key\])/pass/"
  "research_report_unknown_as_value|research/run.py|s/c\[\"value\"\] if verdict == \"verified\" else \"UNKNOWN\"/c[\"value\"]/"
  # Cycle 8 (cycle-008-scheduler): evidence hardening
  "integrity_missing_check_off|integrity/crosscheck.py|s/missing = sorted(s for s, mac in anchored.items() if rows.get(s) != mac)/missing = []/"
  "integrity_unanchored_off|integrity/crosscheck.py|s/unanchored = sorted(s for s in rows if s not in anchored)/unanchored = []/"
  "ledger_read_mac_check_off|research/ledger.py|s/if not hmac.compare_digest(self._mac(c), c\[\"mac\"\]):/if False:/"
  "ledger_chain_check_off|research/ledger.py|s/if row\[\"seq\"\] != i or row\[\"prev_mac\"\] != prev:/if False:/"
  "ledger_row_mac_check_off|research/ledger.py|s/if not hmac.compare_digest(self._mac(row), row\[\"mac\"\]):/if False:/"
  "claim_anchor_off|research/handlers.py|s/c.audit.append(\"claim_recorded\", /(lambda *a, **k: None)(\"claim_recorded\", /"
  "report_ignores_integrity|research/run.py|s/if not chain_ok:  # a tampered/if False:  # a tampered/"
  # Cycle 8: scheduler
  "sched_kill_ignored|scheduler/engine.py|s/        if self.kill.engaged():/        if False:/"
  "sched_risk_ceiling_off|scheduler/engine.py|s/if registered.risk > UNATTENDED_MAX_RISK:/if False:/"
  "sched_scheduler_cap_ok|scheduler/engine.py|s/ or cap.startswith(\"cap.scheduler.\"):/:/"
  "sched_grant_check_off|scheduler/engine.py|s/if not self.cp.policy.evaluate(actor, cap).allowed:/if False:/"
  "sched_step_keys_open|scheduler/engine.py|s/ <= set(st) <= {\"capability\", \"args\", \"cost\"}/ <= set(st)/"
  "sched_opid_not_slot_bound|scheduler/engine.py|s/f\"job:{job_id}:{slot}:{i}\"/f\"job:{job_id}:{slot}:{i}:{__import__('os').urandom(4).hex()}\"/"
  "sched_live_lease_ignored|scheduler/engine.py|s/if self.store.live(now) >= self.max_concurrent:/if False:/"
  "sched_overlap_off|scheduler/engine.py|s/if self.store.live(now, job_id):/if False:/"
  "sched_backwards_unaudited|scheduler/engine.py|s/if last is not None and now < last - self.tolerance:/if False:/"
  "sched_missed_unbounded|scheduler/engine.py|s/limit = 1 if spec\[\"missed\"\] == \"skip\" else spec\[\"catch_up_max\"\]/limit = 10**6/"
  "sched_budget_ignored|scheduler/engine.py|s/Budget(b\[\"max_steps\"\], b\[\"max_wall_s\"\], b\[\"max_cost\"\])/Budget(10**6, 1e12, 1e12)/"
  "sched_job_mac_check_off|scheduler/store.py|s/ok = hmac.compare_digest(self.mac(job_id, body), mac)/ok = True/"
  "sched_claim_replaces|scheduler/store.py|s/INSERT OR IGNORE INTO runs/INSERT OR REPLACE INTO runs/"
  "sched_takeover_not_cas|scheduler/store.py|s/AND lease_until=?\", (owner, lease_until, job_id, slot, old_lease)/AND lease_until>=?\", (owner, lease_until, job_id, slot, old_lease)/"
  "sched_live_leases_recovered|scheduler/store.py|s/ and r\[\"lease_until\"\] <= now\]/]/"
  "schedule_dom_dow_and|scheduler/schedule.py|s/return vals\[2\] in dom or vals\[4\] in dow/return vals[2] in dom and vals[4] in dow/"
  "schedule_interval_includes_after|scheduler/schedule.py|s/range(max(lo + 1, hi - limit + 1), hi + 1)/range(max(lo, hi - limit + 1), hi + 1)/"
)
fail=0; killed=0; ran=0
ONLY="${MUTANT_ONLY:-}"  # optional regex for local iteration; CI runs all
SHARD="${MUTANT_SHARD:-0/1}"  # "i/n": run mutants whose index % n == i (CI: one shard per Python job, D-0023)
SI="${SHARD%/*}"; SN="${SHARD#*/}"
if ! [[ "$SI" =~ ^[0-9]+$ && "$SN" =~ ^[1-9][0-9]*$ ]] || [ "$SI" -ge "$SN" ]; then echo "bad MUTANT_SHARD=$SHARD"; exit 2; fi
idx=-1; expected=0
for m in "${MUTANTS[@]}"; do
  idx=$((idx+1))
  if [ $((idx % SN)) -ne "$SI" ]; then continue; fi
  expected=$((expected+1))
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
echo "MUTATION mutants=$ran killed=$killed shard=$SHARD total_defined=${#MUTANTS[@]}${ONLY:+ (filtered: $ONLY)}"
[ -z "$ONLY" ] && [ "$ran" -ne "$expected" ] && fail=1
exit $fail
