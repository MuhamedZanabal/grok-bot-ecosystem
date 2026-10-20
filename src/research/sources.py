"""Source registry (cycle-007, D-0020): the spec's Source Handling rules, persisted.

* The URL is stored byte-exact; source_id = "src:" + sha256(exact URL bytes)[:32]. There is
  NO normalisation, so a mutated URL (case, slash, query, whitespace) is a different source.
* Every source starts candidate / trusted=0 / execution_allowed=0. The table CHECKs make the
  latter two impossible to change; `accepted` is about evidence, never about trust.
* Lifecycle: candidate->evaluating->accepted|rejected|deferred, deferred->evaluating. Every
  transition needs >= 1 evidence pointer (validated by the capability handler) and is
  appended to `transitions` (never updated) and audited by the handler.
"""

import hashlib
import re
import sqlite3
import threading

from control_plane.audit import contains_secret
from control_plane.canonical import canonical_json

STATUSES = ("candidate", "evaluating", "accepted", "rejected", "deferred")
LEGAL = {("candidate", "evaluating"), ("evaluating", "accepted"), ("evaluating", "rejected"),
         ("evaluating", "deferred"), ("deferred", "evaluating")}
_URL_OK = re.compile(r"[\x21-\x7e]{1,2048}")
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sources (
         source_id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
         status TEXT NOT NULL DEFAULT 'candidate',
         trusted INTEGER NOT NULL DEFAULT 0 CHECK (trusted = 0),
         execution_allowed INTEGER NOT NULL DEFAULT 0 CHECK (execution_allowed = 0),
         created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS transitions (
         n INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, from_status TEXT NOT NULL,
         to_status TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL, at REAL NOT NULL)""",
)
_COLS = ("source_id", "url", "kind", "status", "trusted", "execution_allowed", "created_at", "updated_at")


class SourceError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def source_id_for(url: str) -> str:
    return "src:" + hashlib.sha256(url.encode()).hexdigest()[:32]


class SourceRegistry:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA synchronous=FULL")
        for stmt in _SCHEMA:
            self._db.execute(stmt)

    def close(self) -> None:
        self._db.close()

    def register(self, url: str, kind: str, now: float) -> tuple[dict, bool]:
        """Returns (row, created). Re-registering the exact URL returns the existing row."""
        if not isinstance(url, str) or not _URL_OK.fullmatch(url):
            raise SourceError("bad_url")
        if contains_secret(url):
            raise SourceError("url_contains_secret")
        sid = source_id_for(url)
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO sources (source_id, url, kind, created_at, updated_at) "
                                   "VALUES (?, ?, ?, ?, ?)", (sid, url, kind, now, now))
            created = cur.rowcount == 1
        return self.get(sid), created

    def get(self, source_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute(f"SELECT {', '.join(_COLS)} FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if r is None:
            return None
        row = dict(zip(_COLS, r))
        row["trusted"], row["execution_allowed"] = bool(row["trusted"]), bool(row["execution_allowed"])
        return row

    def all(self) -> list[dict]:
        with self._lock:
            ids = [r[0] for r in self._db.execute("SELECT source_id FROM sources ORDER BY source_id")]
        return [self.get(i) for i in ids]

    def transition(self, source_id: str, to: str, reason: str, evidence: list, now: float) -> dict:
        row = self.get(source_id)
        if row is None:
            raise SourceError("unknown_source")
        if (row["status"], to) not in LEGAL:
            raise SourceError("illegal_transition")
        if not evidence:
            raise SourceError("transition_without_evidence")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cur = self._db.execute("UPDATE sources SET status=?, updated_at=? WHERE source_id=? AND status=?",
                                       (to, now, source_id, row["status"]))
                if cur.rowcount != 1:
                    raise SourceError("concurrent_transition")
                self._db.execute("INSERT INTO transitions (source_id, from_status, to_status, reason, evidence, at) "
                                 "VALUES (?, ?, ?, ?, ?, ?)",
                                 (source_id, row["status"], to, reason, canonical_json(evidence), now))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return {"source_id": source_id, "from": row["status"], "to": to}

    def transitions(self, source_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT from_status, to_status, reason, evidence, at FROM transitions "
                                    "WHERE source_id=? ORDER BY n", (source_id,)).fetchall()
        return [{"from": a, "to": b, "reason": c, "evidence": d, "at": e} for a, b, c, d, e in rows]
