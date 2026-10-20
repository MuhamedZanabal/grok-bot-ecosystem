"""Persistent scheduler state (SQLite, cycle-008, D-0022).

jobs : MAC'd definition (actor, steps, schedule, policy, budget) + mutable high_water
runs : one row per (job_id, slot) - PRIMARY KEY makes a slot claimable exactly once,
       with a lease (owner, lease_until) so a crashed run can be re-driven
gaps : missed slots recorded instead of run (skip / catch-up cap), one row per decision
meta : last observed wall clock (backwards-clock detection)
"""

import hashlib
import hmac
import json
import sqlite3
import threading

from control_plane.canonical import canonical_json

TERMINAL = ("succeeded", "failed", "killed", "budget_exhausted", "in_doubt", "skipped_overlap", "quarantined")
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, spec TEXT NOT NULL, mac TEXT NOT NULL,
         high_water INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS runs (job_id TEXT NOT NULL, slot INTEGER NOT NULL, state TEXT NOT NULL,
         owner TEXT NOT NULL, lease_until INTEGER, started_at INTEGER NOT NULL, finished_at INTEGER,
         steps_done INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '', PRIMARY KEY (job_id, slot))""",
    """CREATE TABLE IF NOT EXISTS gaps (job_id TEXT NOT NULL, upto INTEGER NOT NULL, after INTEGER NOT NULL,
         skipped INTEGER NOT NULL, policy TEXT NOT NULL, PRIMARY KEY (job_id, upto))""",
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v INTEGER NOT NULL)",
)
_RUN = ("job_id", "slot", "state", "owner", "lease_until", "started_at", "finished_at", "steps_done", "reason")


class JobStore:
    def __init__(self, path: str, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("job store MAC key must be >= 32 bytes")
        self._key = key
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA synchronous=FULL")
        for stmt in _SCHEMA:
            self._db.execute(stmt)

    def close(self) -> None:
        self._db.close()

    def _x(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args)

    def mac(self, job_id: str, spec: str) -> str:
        return hmac.new(self._key, canonical_json([job_id, spec]).encode(), hashlib.sha256).hexdigest()

    # ---------------------------------------------------------------- jobs
    def put_job(self, job_id: str, spec: dict, high_water: int) -> bool:
        body = canonical_json(spec)
        return self._x("INSERT OR IGNORE INTO jobs VALUES (?, ?, ?, ?)",
                       (job_id, body, self.mac(job_id, body), high_water)).rowcount == 1

    def jobs(self) -> list[tuple[str, dict | None, int]]:
        """[(job_id, spec or None if its MAC fails, high_water)] in job_id order."""
        out = []
        for job_id, body, mac, hw in self._x("SELECT job_id, spec, mac, high_water FROM jobs ORDER BY job_id").fetchall():
            ok = hmac.compare_digest(self.mac(job_id, body), mac)
            out.append((job_id, json.loads(body) if ok else None, hw))
        return out

    def job(self, job_id: str):
        return next((j for j in self.jobs() if j[0] == job_id), None)

    def advance(self, job_id: str, slot: int) -> None:
        self._x("UPDATE jobs SET high_water=MAX(high_water, ?) WHERE job_id=?", (slot, job_id))

    # ---------------------------------------------------------------- runs
    def claim(self, job_id, slot, owner, now, lease_until, state="running", reason="") -> bool:
        return self._x("INSERT OR IGNORE INTO runs (job_id, slot, state, owner, lease_until, started_at, reason) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (job_id, slot, state, owner, lease_until if state == "running" else None, now,
                        reason)).rowcount == 1

    def take_over(self, job_id, slot, old_lease, owner, lease_until) -> bool:
        """Compare-and-swap an expired lease so exactly one instance re-drives a crashed run."""
        return self._x("UPDATE runs SET owner=?, lease_until=? WHERE job_id=? AND slot=? AND state='running' "
                       "AND lease_until=?", (owner, lease_until, job_id, slot, old_lease)).rowcount == 1

    def step_done(self, job_id, slot, n) -> None:
        self._x("UPDATE runs SET steps_done=MAX(steps_done, ?) WHERE job_id=? AND slot=?", (n, job_id, slot))

    def finish(self, job_id, slot, owner, state, reason, now) -> bool:
        return self._x("UPDATE runs SET state=?, reason=?, finished_at=?, lease_until=NULL WHERE job_id=? AND "
                       "slot=? AND owner=? AND state='running'", (state, reason[:200], now, job_id, slot,
                                                                  owner)).rowcount == 1

    def runs(self, job_id: str | None = None) -> list[dict]:
        where, args = ("WHERE job_id=?", (job_id,)) if job_id else ("", ())
        return [dict(zip(_RUN, r)) for r in self._x(f"SELECT {', '.join(_RUN)} FROM runs {where} "
                                                    "ORDER BY job_id, slot", args).fetchall()]

    def live(self, now: int, job_id: str | None = None) -> int:
        q = "SELECT COUNT(*) FROM runs WHERE state='running' AND lease_until > ?"
        return self._x(q + (" AND job_id=?" if job_id else ""), (now, job_id) if job_id else (now,)).fetchone()[0]

    def expired(self, now: int) -> list[dict]:
        return [r for r in self.runs() if r["state"] == "running" and r["lease_until"] is not None
                and r["lease_until"] <= now]

    # ---------------------------------------------------------------- gaps / meta
    def gap(self, job_id, after, upto, skipped, policy) -> bool:
        return self._x("INSERT OR IGNORE INTO gaps VALUES (?, ?, ?, ?, ?)",
                       (job_id, upto, after, skipped, policy)).rowcount == 1

    def gaps(self, job_id: str) -> list[dict]:
        rows = self._x("SELECT after, upto, skipped, policy FROM gaps WHERE job_id=? ORDER BY upto", (job_id,))
        return [dict(zip(("after", "upto", "skipped", "policy"), r)) for r in rows.fetchall()]

    def observe_wall(self, now: int) -> int | None:
        """Record max(last, now); return the previous high-water wall time."""
        with self._lock:
            r = self._db.execute("SELECT v FROM meta WHERE k='wall'").fetchone()
            self._db.execute("INSERT INTO meta VALUES ('wall', ?) ON CONFLICT(k) DO UPDATE SET v=MAX(v, excluded.v)",
                             (now,))
        return r[0] if r else None
