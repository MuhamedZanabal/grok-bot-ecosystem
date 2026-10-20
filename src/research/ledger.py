"""Claim ledger + branch bookkeeping (cycle-007, D-0020). Persisted in SQLite.

A claim is (branch_id, subject, attribute, value) plus evidence pointers
[{source_id, record_id, content_sha256, quote}]. Admission and every read go through
research.verifier; this module only stores. Branches record a comparable metric
(name + direction) and the consecutive non-superior pass count used by branch_closed.
"""

import hashlib
import json
import sqlite3
import threading

from control_plane.canonical import canonical_json

CLOSE_AFTER = 3  # consecutive discovery passes with no superior candidate
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS claims (
         claim_id TEXT PRIMARY KEY, branch_id TEXT NOT NULL, subject TEXT NOT NULL, attribute TEXT NOT NULL,
         value TEXT NOT NULL, origin TEXT NOT NULL, evidence TEXT NOT NULL, created_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS branches (
         branch_id TEXT PRIMARY KEY, question TEXT NOT NULL, metric TEXT NOT NULL, direction TEXT NOT NULL,
         min_delta REAL NOT NULL, config TEXT NOT NULL, best_value REAL, best_claim TEXT, best_source TEXT,
         stale_passes INTEGER NOT NULL DEFAULT 0, passes INTEGER NOT NULL DEFAULT 0,
         status TEXT NOT NULL DEFAULT 'open', closed_reason TEXT)""",
    """CREATE TABLE IF NOT EXISTS passes (
         branch_id TEXT NOT NULL, n INTEGER NOT NULL, superior INTEGER NOT NULL, pass_best REAL,
         pass_best_claim TEXT, incumbent REAL, PRIMARY KEY (branch_id, n))""",
)
_CLAIM = ("claim_id", "branch_id", "subject", "attribute", "value", "origin", "evidence", "created_at")
_BRANCH = ("branch_id", "question", "metric", "direction", "min_delta", "config", "best_value", "best_claim",
           "best_source", "stale_passes", "passes", "status", "closed_reason")


def claim_id_for(branch_id, subject, attribute, value, evidence) -> str:
    body = canonical_json({"b": branch_id, "s": subject, "a": attribute, "v": value, "e": evidence})
    return "clm:" + hashlib.sha256(body.encode()).hexdigest()[:32]


def better(value: float, incumbent: float | None, direction: str, min_delta: float) -> bool:
    if incumbent is None:
        return True
    return value < incumbent - min_delta if direction == "min" else value > incumbent + min_delta


class ClaimLedger:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA synchronous=FULL")
        for stmt in _SCHEMA:
            self._db.execute(stmt)

    def close(self) -> None:
        self._db.close()

    # ---------------------------------------------------------------- claims
    def add(self, branch_id, subject, attribute, value, origin, evidence, now) -> tuple[str, bool]:
        cid = claim_id_for(branch_id, subject, attribute, value, evidence)
        with self._lock:
            cur = self._db.execute(f"INSERT OR IGNORE INTO claims ({', '.join(_CLAIM)}) VALUES (?,?,?,?,?,?,?,?)",
                                   (cid, branch_id, subject, attribute, value, origin, canonical_json(evidence), now))
        return cid, cur.rowcount == 1

    def _claims(self, where: str, args: tuple) -> list[dict]:
        with self._lock:
            rows = self._db.execute(f"SELECT {', '.join(_CLAIM)} FROM claims WHERE {where} ORDER BY claim_id",
                                    args).fetchall()
        out = []
        for r in rows:
            c = dict(zip(_CLAIM, r))
            c["evidence"] = json.loads(c["evidence"])
            out.append(c)
        return out

    def get(self, claim_id: str) -> dict | None:
        rows = self._claims("claim_id=?", (claim_id,))
        return rows[0] if rows else None

    def claims(self, branch_id: str) -> list[dict]:
        return self._claims("branch_id=?", (branch_id,))

    def peers(self, claim: dict) -> list[dict]:
        """Other claims in the same branch about the same (subject, attribute)."""
        return [c for c in self._claims("branch_id=? AND subject=? AND attribute=?",
                                        (claim["branch_id"], claim["subject"], claim["attribute"]))
                if c["claim_id"] != claim["claim_id"]]

    # ---------------------------------------------------------------- branches
    def open_branch(self, branch_id, question, metric, direction, min_delta, config) -> dict:
        if direction not in ("min", "max"):
            raise ValueError("direction must be min or max")
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO branches (branch_id, question, metric, direction, min_delta, "
                             "config) VALUES (?,?,?,?,?,?)",
                             (branch_id, question, metric, direction, float(min_delta), canonical_json(config)))
        return self.branch(branch_id)

    def branch(self, branch_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute(f"SELECT {', '.join(_BRANCH)} FROM branches WHERE branch_id=?",
                                 (branch_id,)).fetchone()
        if r is None:
            return None
        b = dict(zip(_BRANCH, r))
        b["config"] = json.loads(b["config"])
        return b

    def passes(self, branch_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT n, superior, pass_best, pass_best_claim, incumbent FROM passes "
                                    "WHERE branch_id=? ORDER BY n", (branch_id,)).fetchall()
        return [{"n": a, "superior": bool(b), "pass_best": c, "pass_best_claim": d, "incumbent": e}
                for a, b, c, d, e in rows]

    def record_pass(self, branch_id, pass_best: float | None, pass_best_claim, pass_best_source) -> dict:
        """Append one discovery pass; returns the updated branch (closed after CLOSE_AFTER stale passes)."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                b = dict(zip(_BRANCH, self._db.execute(f"SELECT {', '.join(_BRANCH)} FROM branches WHERE "
                                                       "branch_id=?", (branch_id,)).fetchone()))
                if b["status"] != "open":
                    raise ValueError("branch_closed")
                superior = pass_best is not None and better(pass_best, b["best_value"], b["direction"], b["min_delta"])
                n = b["passes"] + 1
                self._db.execute("INSERT INTO passes VALUES (?,?,?,?,?,?)",
                                 (branch_id, n, int(superior), pass_best, pass_best_claim, b["best_value"]))
                if superior:
                    self._db.execute("UPDATE branches SET best_value=?, best_claim=?, best_source=?, stale_passes=0, "
                                     "passes=? WHERE branch_id=?",
                                     (pass_best, pass_best_claim, pass_best_source, n, branch_id))
                else:
                    stale = b["stale_passes"] + 1
                    status = "closed" if stale >= CLOSE_AFTER else "open"
                    reason = f"no_superior_candidate_in_{CLOSE_AFTER}_consecutive_passes" if status == "closed" else None
                    self._db.execute("UPDATE branches SET stale_passes=?, passes=?, status=?, closed_reason=? "
                                     "WHERE branch_id=?", (stale, n, status, reason, branch_id))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return self.branch(branch_id)
