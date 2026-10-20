"""Outbound queue, suppression list, send log and outcome tracker (SQLite, cycle-010).

drafts     : content (lead, channel, recipient, subject, body, hash, template, personalization,
             meta) is HMAC'd; a row whose MAC fails is treated as tampered and never sent.
             state: pending_approval -> sent | rejected | failed
sends      : PRIMARY KEY draft_id -> a draft is sent at most once
reservations (cycle-011): two-phase handoff. UNIQUE draft_id -> a draft is planned at most once;
             reserved -> sent | failed | cancelled (opt-out) | in_doubt (expired unconfirmed; still
             counts against caps, never re-planned, may still be confirmed late)
grants     : standing-permission records, MAC'd by the approval issuer (not the outbox key)
suppression: normalized recipient -> reason (bounced, closed, opted_out, operator)
outcomes   : per lead reply/outcome, UNKNOWN by default; revenue is never stored as a number
"""

import hashlib
import hmac
import json
import sqlite3
import threading

from control_plane.canonical import canonical_json

from .leads import norm_email

OUTCOMES = ("UNKNOWN", "no_reply", "replied", "opted_out", "demo_booked", "pilot_started", "paid_unverified")
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS drafts (draft_id TEXT PRIMARY KEY, lead_id TEXT NOT NULL, channel TEXT NOT NULL,
         recipient TEXT NOT NULL, recipient_norm TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
         draft_hash TEXT NOT NULL, template_id TEXT NOT NULL, personalization TEXT NOT NULL, meta TEXT NOT NULL,
         state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, mac TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sends (draft_id TEXT PRIMARY KEY, lead_id TEXT NOT NULL,
         recipient_norm TEXT NOT NULL, day TEXT NOT NULL, sent_at REAL NOT NULL, transport TEXT NOT NULL,
         message_id TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS suppression (recipient_norm TEXT PRIMARY KEY, reason TEXT NOT NULL,
         source TEXT NOT NULL, added_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS reservations (reservation_id TEXT PRIMARY KEY, draft_id TEXT NOT NULL UNIQUE,
         draft_hash TEXT NOT NULL, lead_id TEXT NOT NULL, recipient_norm TEXT NOT NULL, day TEXT NOT NULL,
         reserved_at REAL NOT NULL, expires_at REAL NOT NULL, grant_ref TEXT NOT NULL, body_sha256 TEXT NOT NULL,
         body_file TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, message_id TEXT UNIQUE, detail TEXT NOT NULL DEFAULT '',
         updated_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS grants (ref TEXT PRIMARY KEY, template_id TEXT NOT NULL, granted_on TEXT NOT NULL,
         statement TEXT NOT NULL, max_sends INTEGER NOT NULL, recorded_at REAL NOT NULL, revoked_at REAL, mac TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS outcomes (lead_id TEXT PRIMARY KEY, outcome TEXT NOT NULL, evidence TEXT NOT NULL,
         updated_at REAL NOT NULL)""",
)
_LIVE = ("SELECT recipient_norm, lead_id, day FROM sends UNION ALL SELECT recipient_norm, lead_id, day "
         "FROM reservations WHERE state IN ('reserved', 'in_doubt')")     # what counts against caps
_MACED = ("draft_id", "lead_id", "channel", "recipient", "subject", "body", "draft_hash", "template_id",
          "personalization", "meta")
_COLS = _MACED + ("recipient_norm", "state", "reason", "created_at", "mac")


class OutboxStore:
    def __init__(self, path: str, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("outbox MAC key must be >= 32 bytes")
        self._key, self.lock = key, threading.RLock()
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA synchronous=FULL")
        for s in _SCHEMA:
            self.db.execute(s)

    def close(self) -> None:
        self.db.close()

    def _mac(self, row: dict) -> str:
        return hmac.new(self._key, canonical_json({k: row[k] for k in _MACED}).encode(), hashlib.sha256).hexdigest()

    def put_draft(self, d: dict, now: float) -> tuple[dict, bool]:
        row = {**d, "personalization": canonical_json(d["personalization"]), "meta": canonical_json(d["meta"]),
               "recipient_norm": norm_email(d["recipient"]), "state": "pending_approval", "reason": "",
               "created_at": now}
        row["mac"] = self._mac(row)
        with self.lock:
            cur = self.db.execute(f"INSERT OR IGNORE INTO drafts ({','.join(_COLS)}) VALUES "
                                  f"({','.join('?' * len(_COLS))})", tuple(row[k] for k in _COLS))
        return self.draft(d["draft_id"]), cur.rowcount == 1

    def draft(self, draft_id: str) -> dict | None:
        """The row with `intact` = MAC verifies; content of a non-intact row must not be used."""
        r = self.db.execute(f"SELECT {','.join(_COLS)} FROM drafts WHERE draft_id=?", (draft_id,)).fetchone()
        if r is None:
            return None
        row = dict(zip(_COLS, r))
        row["intact"] = hmac.compare_digest(self._mac(row), row["mac"])
        return row

    def drafts(self, state: str | None = None) -> list[dict]:
        ids = self.db.execute("SELECT draft_id FROM drafts" + (" WHERE state=?" if state else "") +
                              " ORDER BY created_at, draft_id", (state,) if state else ()).fetchall()
        out = []
        for (i,) in ids:
            d = self.draft(i)
            if d["intact"]:
                d["personalization"], d["meta"] = json.loads(d["personalization"]), json.loads(d["meta"])
            out.append(d)
        return out

    def suppress(self, recipient: str, reason: str, source: str, now: float) -> None:
        n = norm_email(recipient) or recipient.strip().lower()
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO suppression VALUES (?,?,?,?)", (n, reason, source, now))
            self.db.execute("UPDATE drafts SET state='cancelled', reason=? WHERE draft_id IN (SELECT draft_id FROM "
                            "reservations WHERE recipient_norm=? AND state='reserved')", (reason, n))
            self.db.execute("UPDATE reservations SET state='cancelled', detail=?, updated_at=? "
                            "WHERE recipient_norm=? AND state='reserved'", (reason, now, n))

    def suppressed(self, recipient: str) -> str | None:
        r = self.db.execute("SELECT reason FROM suppression WHERE recipient_norm=?",
                            (norm_email(recipient) or recipient.strip().lower(),)).fetchone()
        return r[0] if r else None

    def counts(self, recipient_norm: str, lead_id: str, day: str) -> dict:
        q = lambda w, a: self.db.execute(f"SELECT COUNT(*) FROM ({_LIVE}) WHERE {w}", a).fetchone()[0]
        return {"day": q("day=?", (day,)), "recipient_total": q("recipient_norm=? OR lead_id=?", (recipient_norm, lead_id)),
                "recipient_day": q("(recipient_norm=? OR lead_id=?) AND day=?", (recipient_norm, lead_id, day))}

    def outcome(self, lead_id: str) -> dict:
        r = self.db.execute("SELECT outcome, evidence, updated_at FROM outcomes WHERE lead_id=?", (lead_id,)).fetchone()
        o = {"outcome": r[0], "evidence": r[1], "updated_at": r[2]} if r else \
            {"outcome": "UNKNOWN", "evidence": "", "updated_at": None}
        o["reply_state"] = {"UNKNOWN": "UNKNOWN", "no_reply": "no_reply"}.get(o["outcome"], "replied")
        o["revenue"] = {"value": None, "provenance": "unknown", "unit": "BHD", "source": "unverified:no_reconciler"}
        return o

    def reservation(self, rid: str) -> dict | None:
        cur = self.db.execute("SELECT * FROM reservations WHERE reservation_id=?", (rid,))
        r = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], r)) if r else None

    def reservations(self, state: str | None = None) -> list[dict]:
        cur = self.db.execute("SELECT * FROM reservations" + (" WHERE state=?" if state else "") +
                              " ORDER BY reserved_at, reservation_id", (state,) if state else ())
        return [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]

    def sweep(self, now: float) -> int:
        """Expired unconfirmed reservations become in_doubt: never re-planned, still counted."""
        with self.lock:
            n = self.db.execute("UPDATE reservations SET state='in_doubt', updated_at=? WHERE state='reserved' "
                                "AND expires_at <= ?", (now, now)).rowcount
            self.db.execute("UPDATE drafts SET state='in_doubt' WHERE state='reserved' AND draft_id IN "
                            "(SELECT draft_id FROM reservations WHERE state='in_doubt')")
        return n

    def grant(self, ref: str) -> dict | None:
        cur = self.db.execute("SELECT * FROM grants WHERE ref=?", (ref,))
        r = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], r)) if r else None

    def grant_used(self, ref: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM reservations WHERE grant_ref=?", (ref,)).fetchone()[0]
