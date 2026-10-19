"""Persistent, versioned registry trust state (SQLite).

`tool_events` is append-only: every register/import/accept/reject is a row whose
`version` is monotonically increasing. `tools` holds current state, written in the same
transaction. Accepted tools persist their WorkerSpec so they are re-bound (sandboxed)
after a restart; candidates stay non-executable across restarts.
"""

import sqlite3
import threading
import time

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS tool_events (
         version INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, event TEXT NOT NULL,
         capability_id TEXT NOT NULL, source TEXT NOT NULL,
         trust TEXT NOT NULL CHECK (trust IN ('candidate','accepted','rejected')),
         risk INTEGER NOT NULL, description TEXT NOT NULL, schema_json TEXT NOT NULL,
         worker_json TEXT, reason TEXT NOT NULL, by TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS tools (
         capability_id TEXT PRIMARY KEY, source TEXT NOT NULL, trust TEXT NOT NULL, risk INTEGER NOT NULL,
         description TEXT NOT NULL, schema_json TEXT NOT NULL, worker_json TEXT, reason TEXT NOT NULL,
         version INTEGER NOT NULL)""",
)
_COLS = ("capability_id", "source", "trust", "risk", "description", "schema_json", "worker_json", "reason", "version")


class RegistryStore:
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

    def __del__(self) -> None:  # registries are often short-lived; never leak the connection
        try:
            self._db.close()
        except Exception:
            pass

    def record(self, event: str, *, capability_id: str, source: str, trust: str, risk: int,
               description: str, schema_json: str, worker_json: str | None, reason: str, by: str) -> int:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cur = self._db.execute(
                    "INSERT INTO tool_events (ts, event, capability_id, source, trust, risk, description,"
                    " schema_json, worker_json, reason, by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), event, capability_id, source, trust, int(risk), description, schema_json,
                     worker_json, reason, by))
                v = cur.lastrowid
                self._db.execute("INSERT OR REPLACE INTO tools VALUES (?,?,?,?,?,?,?,?,?)",
                                 (capability_id, source, trust, int(risk), description, schema_json,
                                  worker_json, reason, v))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return v

    def current(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(f"SELECT {', '.join(_COLS)} FROM tools ORDER BY version").fetchall()
        return [dict(zip(_COLS, r)) for r in rows]

    def history(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT version, event, capability_id, trust, risk, by, reason"
                                    " FROM tool_events ORDER BY version").fetchall()
        return [dict(zip(("version", "event", "capability_id", "trust", "risk", "by", "reason"), r)) for r in rows]

    def version(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COALESCE(MAX(version),0) FROM tool_events").fetchone()[0])
