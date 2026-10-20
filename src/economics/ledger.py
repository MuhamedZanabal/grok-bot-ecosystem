"""MAC-chained economics ledger + pre-execution spend guard (cycle-009, D-0024).

Rows (HMAC-SHA256 chain seq/prev_mac/mac under an operator key; (seq, mac) anchored in the
audit log as `economics_recorded`, covered by integrity.crosscheck):
  reservation : written BEFORE execution, after cumulative caps pass, holding the configured
                per-call estimate (caps bound estimated spend; billed spend is UNKNOWN)
  operation   : written after a governed operation ran (executed/failed/timed_out), all 7
                economic fields + host-measured meter (wall time, tool calls, proxy egress bytes)
  model_call  : one provider call (tokens from provider `last_usage` when present, else unknown)
actual_revenue is always unknown: no reconciler exists, so any attempt to set it is refused.
"""

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass

from control_plane.audit import redact
from control_plane.canonical import canonical_json

from .quantity import QuantityError, check, estimate, measured, total, unknown

GENESIS = "0" * 64
FIELDS = ("expected_value", "estimated_cost", "actual_cost", "expected_revenue", "actual_revenue",
          "risk", "confidence")
SCOPE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SCHEMA = """CREATE TABLE IF NOT EXISTS entries (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL,
  subject TEXT NOT NULL, actor TEXT NOT NULL, item TEXT NOT NULL, day TEXT NOT NULL, run TEXT, job TEXT,
  usd REAL, body TEXT NOT NULL, created_at REAL NOT NULL, prev_mac TEXT NOT NULL, mac TEXT NOT NULL)"""
_COLS = ("seq", "kind", "subject", "actor", "item", "day", "run", "job", "usd", "body", "created_at",
         "prev_mac", "mac")


class RevenueRefused(Exception):
    pass


@dataclass(frozen=True)
class Caps:
    """None = no cap. USD caps bound reserved configured estimates; *_calls bound call counts."""
    day_usd: float | None = None          # per actor per UTC day
    day_usd_global: float | None = None   # all actors per UTC day
    run_usd: float | None = None          # per run scope (required when set)
    job_usd: float | None = None          # per job scope (when present)
    day_calls: int | None = None          # per actor per UTC day
    run_calls: int | None = None


@dataclass(frozen=True)
class Pricing:
    per_call_usd: dict      # item (capability id or model:<provider>) -> configured estimate
    token_usd: dict         # provider -> {"in_per_1k": x, "out_per_1k": y} configured estimate
    expected: dict          # item -> {"expected_value", "expected_revenue", "confidence"}


class EconomicsLedger:
    def __init__(self, path: str, key: bytes, audit, *, caps: Caps = Caps(), pricing: Pricing | None = None,
                 clock=time.time) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("economics ledger MAC key must be >= 32 bytes")
        self._key, self._audit, self.caps, self._clock = key, audit, caps, clock
        self.pricing = pricing or Pricing({}, {}, {})
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(_SCHEMA)
        self._db.execute("CREATE INDEX IF NOT EXISTS e_day ON entries(day, kind)")
        self._head = None                      # last (seq, mac) this process wrote
        self._dv = None                        # PRAGMA data_version at last full verification
        self._egress = defaultdict(int)        # capability -> proxy-measured bytes (audit listener)
        if audit is not None:
            audit.listeners.append(self.on_audit)

    # ------------------------------------------------------------------ chain
    def _mac(self, row: dict) -> str:
        msg = canonical_json({k: row[k] for k in _COLS if k != "mac"})
        return hmac.new(self._key, msg.encode(), hashlib.sha256).hexdigest()

    def _rows(self, where: str = "", args=()) -> list[dict]:
        cur = self._db.execute(f"SELECT {','.join(_COLS)} FROM entries {where} ORDER BY seq", args)
        return [dict(zip(_COLS, r)) for r in cur.fetchall()]

    def verify_chain(self) -> tuple[bool, int, str]:
        prev = GENESIS
        for i, row in enumerate(self._rows(), 1):
            if row["seq"] != i or row["prev_mac"] != prev:
                return False, row["seq"], "chain_break"
            if not hmac.compare_digest(self._mac(row), row["mac"]):
                return False, row["seq"], "mac_mismatch"
            prev = row["mac"]
        return True, 0, ""

    def anchor_rows(self) -> list[tuple[int, str]]:
        return [(r["seq"], r["mac"]) for r in self._rows()]

    def entries(self) -> list[dict]:
        """Rows whose MAC verifies, body decoded. Tampered rows are never returned."""
        out = []
        for r in self._rows():
            if hmac.compare_digest(self._mac(r), r["mac"]):
                out.append({**{k: r[k] for k in _COLS if k != "body"}, **json.loads(r["body"])})
        return out

    def _intact(self) -> bool:
        """Full chain re-verification, but only when another connection changed the file
        (PRAGMA data_version); our own commits are trusted. Any edit, deletion, reorder or
        truncation of rows we wrote -> False."""
        dv = self._db.execute("PRAGMA data_version").fetchone()[0]
        if dv == self._dv:
            return True
        if self._head is not None:
            last = self._db.execute("SELECT seq, mac FROM entries WHERE seq=?", (self._head[0],)).fetchone()
            if last is None or tuple(last) != self._head:
                return False                               # truncation / rewrite of what we wrote
        if not self.verify_chain()[0]:
            return False
        self._dv = dv
        return True

    def _put(self, kind, subject, actor, item, scope, usd, body: dict) -> dict:
        for f in FIELDS if kind == "operation" else ():
            if f not in body:
                raise QuantityError(f"{f}:missing")
        for f, v in body.items():
            if f == "actual_cost":
                for k, sub in v.items():
                    check(sub, f"actual_cost.{k}")
                    if k != "usd" and sub["provenance"] == "configured_estimate":
                        raise QuantityError(f"actual_cost.{k}:must_be_measured_or_unknown")
            elif f in FIELDS:
                check(v, f)
        if body.get("actual_revenue", {}).get("provenance", "unknown") != "unknown":
            raise RevenueRefused("no_reconciler_registered")
        now = self._clock()
        row = {"kind": kind, "subject": subject, "actor": actor, "item": item, "day": day_of(now),
               "run": scope.get("run"), "job": scope.get("job"), "usd": usd,
               "body": canonical_json(redact(body)), "created_at": now}
        last = self._db.execute("SELECT seq, mac FROM entries ORDER BY seq DESC LIMIT 1").fetchone()
        row["seq"], row["prev_mac"] = (last[0] if last else 0) + 1, last[1] if last else GENESIS
        row["mac"] = self._mac(row)
        self._db.execute(f"INSERT INTO entries ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                         tuple(row[k] for k in _COLS))
        return row

    def _commit_anchor(self, row: dict) -> None:
        self._head = (row["seq"], row["mac"])
        if self._audit is not None:
            self._audit.append("economics_recorded", seq=row["seq"], mac=row["mac"], kind=row["kind"],
                               subject=row["subject"])

    # ------------------------------------------------------------------ guard
    def price(self, item: str) -> dict:
        p = self.pricing.per_call_usd.get(item)
        return estimate(p, "usd", f"pricing.per_call_usd[{item}]") if p is not None else \
            unknown("usd", f"no_price:{item}")

    def admit(self, actor: str, item: str, subject: str, scope: dict, *, reserve: bool = True):
        """-> (ok, reason, estimated_cost). Check + reserve are one IMMEDIATE transaction."""
        est, c = self.price(item), self.caps
        subject, scope = redact(subject), redact(dict(scope))   # ids can be secret-shaped
        usd_capped = any(x is not None for x in (c.day_usd, c.day_usd_global, c.run_usd, c.job_usd))
        if usd_capped and est["value"] is None:
            return False, "cost_unknown", est        # UNKNOWN is never treated as 0 under a cap
        if (c.run_usd is not None or c.run_calls is not None) and not scope.get("run"):
            return False, "run_scope_required", est
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                today, run, job = day_of(self._clock()), scope.get("run", "\0"), scope.get("job", "\0")
                if not self._intact():                     # tampered ledger: fail closed
                    self._db.execute("ROLLBACK")
                    return False, "ledger_integrity", est
                mine_usd, mine_n, day_usd, run_usd, run_n, job_usd = self._db.execute(
                    """SELECT TOTAL(CASE WHEN day=? AND actor=? THEN usd END), TOTAL(day=? AND actor=?),
                              TOTAL(CASE WHEN day=? THEN usd END), TOTAL(CASE WHEN run=? THEN usd END),
                              TOTAL(run=?), TOTAL(CASE WHEN job=? THEN usd END)
                       FROM entries WHERE kind='reservation' AND (day=? OR run=? OR job=?)""",
                    (today, actor, today, actor, today, run, run, job, today, run, job)).fetchone()
                v = est["value"] or 0.0                   # only reached when no USD cap applies or known
                checks = ((c.day_usd, mine_usd + v, "day_usd_cap"),
                          (c.day_usd_global, day_usd + v, "day_usd_global_cap"),
                          (c.run_usd, run_usd + v, "run_usd_cap"),
                          (c.job_usd if scope.get("job") else None, job_usd + v, "job_usd_cap"),
                          (c.day_calls, mine_n + 1, "day_calls_cap"),
                          (c.run_calls, run_n + 1, "run_calls_cap"))
                for cap, would, code in checks:
                    if cap is not None and would > cap + 1e-12:
                        self._db.execute("ROLLBACK")
                        return False, code, est
                if not reserve:
                    self._db.execute("ROLLBACK")
                    return True, "within_caps", est
                row = self._put("reservation", subject, actor, item, scope, est["value"], {"estimated_cost": est})
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
            self._commit_anchor(row)
        return True, "reserved", est

    # ------------------------------------------------------------------ meter
    def on_audit(self, event: str, data: dict) -> None:
        if event == "egress_closed":
            self._egress[data.get("capability", "")] += int(data.get("bytes_out", 0)) + int(data.get("bytes_in", 0))

    def mark(self, item: str) -> int:
        return self._egress[item]

    def _record(self, kind, subject, actor, item, scope, body) -> dict:
        subject, scope = redact(subject), redact(dict(scope))
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._put(kind, subject, actor, item, scope, None, body)
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
            self._commit_anchor(row)
        return row

    def _expected(self, item: str, risk) -> dict:
        e, src = self.pricing.expected.get(item, {}), f"pricing.expected[{item}]"
        g = lambda k, unit: estimate(e[k], unit, src) if e.get(k) is not None else unknown(unit, src)
        return {"expected_value": g("expected_value", "usd"), "expected_revenue": g("expected_revenue", "usd"),
                "confidence": g("confidence", "ratio"),
                "risk": estimate(risk, "risk_level", "policy.capability.risk") if risk is not None
                else unknown("risk_level", "no_policy_risk"),
                "actual_revenue": unknown("usd", "no_reconciler_registered")}

    def record_operation(self, op, actor, cap, scope, status, reason, wall_s, egress_mark, risk) -> dict:
        meter = {"usd": unknown("usd", "no_billing_source_for_tools"),
                 "wall_s": measured(round(wall_s, 6), "s", "host:perf_counter"),
                 "tool_calls": measured(1, "count", "host:control_plane"),
                 "egress_bytes": measured(self._egress[cap] - egress_mark, "bytes", "host:egress_proxy_audit"),
                 "model_calls": measured(0, "count", "host:control_plane")}
        body = {**self._expected(cap, risk), "estimated_cost": self.price(cap), "actual_cost": meter,
                "status": status, "reason": reason}
        return self._record("operation", op, actor, cap, scope, body)

    def record_model_call(self, subject, actor, provider, usage, latency_s, error, scope) -> dict:
        item = f"model:{provider}"
        tin = tout = None
        if isinstance(usage, dict) and error is None:
            a, b = usage.get("input_tokens"), usage.get("output_tokens")
            ok = lambda n: isinstance(n, int) and not isinstance(n, bool) and n >= 0
            tin, tout = (a if ok(a) else None), (b if ok(b) else None)
        tok = lambda n: measured(n, "tokens", "provider:last_usage") if n is not None else \
            unknown("tokens", "provider_reported_no_usage")
        price = self.pricing.token_usd.get(provider)
        usd = estimate(round(tin / 1000 * price["in_per_1k"] + tout / 1000 * price["out_per_1k"], 9), "usd",
                       "measured_usage*pricing.token_usd") if price and tin is not None and tout is not None \
            else unknown("usd", "no_usage_or_no_token_price")
        meter = {"usd": usd, "wall_s": measured(round(latency_s, 6), "s", "host:perf_counter"),
                 "tool_calls": measured(0, "count", "host:runner"),
                 "model_calls": measured(1, "count", "host:runner"), "usage_in": tok(tin), "usage_out": tok(tout)}
        body = {**self._expected(item, None), "estimated_cost": self.price(item), "actual_cost": meter,
                "status": "error" if error else "ok", "reason": error or ""}
        return self._record("model_call", subject, actor, item, scope, body)

    def record_revenue(self, *args, **kwargs):
        """No reconciler exists in cycle-009, so measured revenue cannot be evidenced: refuse."""
        if self._audit is not None:
            self._audit.append("revenue_refused", reason="no_reconciler_registered")
        raise RevenueRefused("no_reconciler_registered")

    # ------------------------------------------------------------------ reporting
    def burn(self, day: str | None = None) -> dict:
        day = day or day_of(self._clock())
        ok, bad_seq, why = self.verify_chain()
        rows = [e for e in self.entries() if e["day"] == day]
        res = [e for e in rows if e["kind"] == "reservation"]
        done = [e for e in rows if e["kind"] in ("operation", "model_call")]
        subjects = {e["subject"] for e in done}
        per_actor = defaultdict(list)
        for e in res:
            per_actor[e["actor"]].append(e["estimated_cost"])
        orphans = sorted(e["subject"] for e in res if e["subject"] not in subjects)  # crashed / in doubt
        pending = [unknown("usd", f"no_outcome:{o}") for o in orphans]
        return {"day": day, "chain_ok": ok, "chain_reason": why, "caps": asdict(self.caps),
                "reserved_usd": total([e["estimated_cost"] for e in res], "usd"),
                "reserved_usd_by_actor": {a: total(v, "usd") for a, v in sorted(per_actor.items())},
                "actual_usd": total([e["actual_cost"]["usd"] for e in done] + pending, "usd"),
                "actual_revenue": total([e["actual_revenue"] for e in done], "usd"),
                "wall_s": total([e["actual_cost"]["wall_s"] for e in done], "s"),
                "egress_bytes": total([e["actual_cost"]["egress_bytes"] for e in done
                                       if "egress_bytes" in e["actual_cost"]], "bytes"),
                "operations": sum(1 for e in done if e["kind"] == "operation"),
                "model_calls": sum(1 for e in done if e["kind"] == "model_call"),
                "reservations_without_outcome": orphans}


def day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))
