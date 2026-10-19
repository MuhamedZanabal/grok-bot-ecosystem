"""Persistent, versioned grant store (SQLite).

Every grant/revoke is an immutable row in `grant_events`; `version` is that row's
monotonically increasing id. `grants` is the current state, updated in the same
transaction. Each grant records the capability's risk level at grant time so that a
later re-registration at a different risk cannot silently inherit the grant.
"""

import sqlite3
import threading
import time

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS grant_events (
         version    INTEGER PRIMARY KEY AUTOINCREMENT,
         ts         REAL NOT NULL,
         action     TEXT NOT NULL CHECK (action IN ('grant', 'revoke')),
         actor      TEXT NOT NULL,
         capability TEXT NOT NULL,
         risk       INTEGER NOT NULL,
         granted_by TEXT NOT NULL,
         reason     TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS grants (
         actor      TEXT NOT NULL,
         capability TEXT NOT NULL,
         risk       INTEGER NOT NULL,
         version    INTEGER NOT NULL,
         PRIMARY KEY (actor, capability))""",
)


class GrantStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        for stmt in _SCHEMA:
            self._db.execute(stmt)

    def close(self) -> None:
        self._db.close()

    def apply(self, action: str, actor: str, capability: str, risk: int, by: str, reason: str) -> int:
        if action not in ("grant", "revoke"):
            raise ValueError("action must be 'grant' or 'revoke'")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cur = self._db.execute(
                    "INSERT INTO grant_events (ts, action, actor, capability, risk, granted_by, reason)"
                    " VALUES (?,?,?,?,?,?,?)", (time.time(), action, actor, capability, int(risk), by, reason))
                version = cur.lastrowid
                if action == "grant":
                    self._db.execute("INSERT OR REPLACE INTO grants VALUES (?,?,?,?)",
                                     (actor, capability, int(risk), version))
                else:
                    self._db.execute("DELETE FROM grants WHERE actor=? AND capability=?", (actor, capability))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return version

    def lookup(self, actor: str, capability: str) -> tuple[int, int] | None:
        """Return (risk_at_grant_time, version) for a live grant, else None."""
        with self._lock:
            row = self._db.execute("SELECT risk, version FROM grants WHERE actor=? AND capability=?",
                                   (actor, capability)).fetchone()
        return (row[0], row[1]) if row else None

    def granted(self, actor: str) -> list[tuple[str, int]]:
        with self._lock:
            return [(c, r) for c, r in self._db.execute(
                "SELECT capability, risk FROM grants WHERE actor=? ORDER BY capability", (actor,))]

    def version(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(version), 0) FROM grant_events").fetchone()
        return int(row[0])

    def history(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT version, action, actor, capability, risk, granted_by, reason"
                " FROM grant_events ORDER BY version").fetchall()
        keys = ("version", "action", "actor", "capability", "risk", "by", "reason")
        return [dict(zip(keys, r)) for r in rows]
