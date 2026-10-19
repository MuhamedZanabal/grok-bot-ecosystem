"""Persistent idempotency ledger (SQLite, WAL).

An operation_id is executed at most once. States:
  PENDING    recorded before the handler runs; if still PENDING when seen again the
             process crashed mid-operation -> caller reports IN_DOUBT (no re-execution).
  SUCCEEDED  result stored; replays return it without re-executing.
  FAILED     handler raised; terminal.
  TIMED_OUT  handler exceeded its deadline; terminal (side effects unknown).
"""

from dataclasses import dataclass
from enum import Enum
import json
import sqlite3
import threading
import time
from typing import Any


class OpState(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class OpRecord:
    operation_id: str
    capability: str
    actor: str
    args_sha256: str
    state: OpState
    result: Any
    error: str | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
  operation_id TEXT PRIMARY KEY,
  capability   TEXT NOT NULL,
  actor        TEXT NOT NULL,
  args_sha256  TEXT NOT NULL,
  state        TEXT NOT NULL,
  result_json  TEXT,
  error        TEXT,
  created_at   REAL NOT NULL,
  updated_at   REAL NOT NULL
)
"""


class IdempotencyLedger:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def get(self, operation_id: str) -> OpRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT operation_id, capability, actor, args_sha256, state, result_json, error"
                " FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
        if row is None:
            return None
        return OpRecord(row[0], row[1], row[2], row[3], OpState(row[4]),
                        json.loads(row[5]) if row[5] is not None else None, row[6])

    def begin(self, operation_id: str, capability: str, actor: str, args_sha256: str) -> bool:
        """Atomically claim operation_id. Returns False if it already existed."""
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO operations VALUES (?,?,?,?,?,NULL,NULL,?,?)",
                (operation_id, capability, actor, args_sha256, OpState.PENDING.value, now, now))
            return cur.rowcount == 1

    def finish(self, operation_id: str, state: OpState, *, result: Any = None,
               error: str | None = None) -> None:
        if state is OpState.PENDING:
            raise ValueError("finish requires a terminal state")
        result_json = json.dumps(result, sort_keys=True) if state is OpState.SUCCEEDED else None
        with self._lock:
            cur = self._db.execute(
                "UPDATE operations SET state=?, result_json=?, error=?, updated_at=?"
                " WHERE operation_id=? AND state=?",
                (state.value, result_json, error, time.time(), operation_id, OpState.PENDING.value))
            if cur.rowcount != 1:
                raise RuntimeError(f"operation {operation_id} not pending")
