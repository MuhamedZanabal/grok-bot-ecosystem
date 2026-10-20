"""Claim ledger + branch bookkeeping (cycle-007, D-0020). Persisted in SQLite.

cycle-008 (D-0022): claims form an HMAC-SHA256 chain (seq, prev_mac, mac) under an operator
key, like memory. Rows whose MAC fails are excluded from every read; verify_chain() detects
edits, deletions and reordering; each new claim's (seq, mac) is anchored in the audit log
by the claim handler so tail truncation is caught by integrity.crosscheck.

A claim is (branch_id, subject, attribute, value) plus evidence pointers
[{source_id, record_id, content_sha256, quote}]. Admission and every read go through
research.verifier; this module only stores. Branches record a comparable metric
(name + direction) and the consecutive non-superior pass count used by branch_closed.
"""

import hashlib
import hmac
import json
import sqlite3
import threading

from control_plane.canonical import canonical_json

CLOSE_AFTER = 3  # consecutive discovery passes with no superior candidate
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS claims (
         claim_id TEXT PRIMARY KEY, branch_id TEXT NOT NULL, subject TEXT NOT NULL, attribute TEXT NOT NULL,
         value TEXT NOT NULL, origin TEXT NOT NULL, evidence TEXT NOT NULL, created_at REAL NOT NULL,
         seq INTEGER NOT NULL UNIQUE, prev_mac TEXT NOT NULL, mac TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS branches (
         branch_id TEXT PRIMARY KEY, question TEXT NOT NULL, metric TEXT NOT NULL, direction TEXT NOT NULL,
         min_delta REAL NOT NULL, config TEXT NOT NULL, best_value REAL, best_claim TEXT, best_source TEXT,
         stale_passes INTEGER NOT NULL DEFAULT 0, passes INTEGER NOT NULL DEFAULT 0,
         status TEXT NOT NULL DEFAULT 'open', closed_reason TEXT)""",
    """CREATE TABLE IF NOT EXISTS passes (
         branch_id TEXT NOT NULL, n INTEGER NOT NULL, superior INTEGER NOT NULL, pass_best REAL,
         pass_best_claim TEXT, incumbent REAL, PRIMARY KEY (branch_id, n))""",
)
_CLAIM = ("claim_id", "branch_id", "subject", "attribute", "value", "origin", "evidence", "created_at", "seq",
          "prev_mac", "mac")
_MAC_FIELDS = _CLAIM[:-1]
GENESIS = "0" * 64
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
    def __init__(self, path: str, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("claim ledger MAC key must be >= 32 bytes")
        self._key = key
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA synchronous=FULL")
        for stmt in _SCHEMA:
            self._db.execute(stmt)

    def close(self) -> None:
        self._db.close()

    # ---------------------------------------------------------------- claims
    def _mac(self, row: dict) -> str:
        return hmac.new(self._key, canonical_json({k: row[k] for k in _MAC_FIELDS}).encode(), hashlib.sha256).hexdigest()

    def add(self, branch_id, subject, attribute, value, origin, evidence, now) -> dict:
        """Returns {claim_id, created, seq, mac}; an existing claim is returned unchanged."""
        cid = claim_id_for(branch_id, subject, attribute, value, evidence)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                old = self._db.execute("SELECT seq, mac FROM claims WHERE claim_id=?", (cid,)).fetchone()
                if old is not None:
                    self._db.execute("COMMIT")
                    return {"claim_id": cid, "created": False, "seq": old[0], "mac": old[1]}
                last = self._db.execute("SELECT seq, mac FROM claims ORDER BY seq DESC LIMIT 1").fetchone()
                row = {"claim_id": cid, "branch_id": branch_id, "subject": subject, "attribute": attribute,
                       "value": value, "origin": origin, "evidence": canonical_json(evidence), "created_at": now,
                       "seq": (last[0] if last else 0) + 1, "prev_mac": last[1] if last else GENESIS}
                row["mac"] = self._mac(row)
                self._db.execute(f"INSERT INTO claims ({', '.join(_CLAIM)}) VALUES ({', '.join('?' * len(_CLAIM))})",
                                 tuple(row[k] for k in _CLAIM))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return {"claim_id": cid, "created": True, "seq": row["seq"], "mac": row["mac"]}

    def _rows(self, where: str = "1=1", args: tuple = (), order: str = "claim_id") -> list[dict]:
        with self._lock:
            rows = self._db.execute(f"SELECT {', '.join(_CLAIM)} FROM claims WHERE {where} ORDER BY {order}",
                                    args).fetchall()
        return [dict(zip(_CLAIM, r)) for r in rows]

    def _claims(self, where: str, args: tuple) -> list[dict]:
        out = []
        for c in self._rows(where, args):
            if not hmac.compare_digest(self._mac(c), c["mac"]):
                continue  # tampered row: never returned (verify_chain reports it)
            c["evidence"] = json.loads(c["evidence"])
            out.append(c)
        return out

    def verify_chain(self) -> tuple[bool, int, str]:
        prev = GENESIS
        rows = self._rows(order="seq")
        for i, row in enumerate(rows, 1):
            if row["seq"] != i or row["prev_mac"] != prev:
                return False, row["seq"], "chain_break"
            if not hmac.compare_digest(self._mac(row), row["mac"]):
                return False, row["seq"], "mac_mismatch"
            prev = row["mac"]
        return True, len(rows), ""

    def anchor_rows(self) -> list[tuple[int, str]]:
        return [(r["seq"], r["mac"]) for r in self._rows(order="seq")]

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
