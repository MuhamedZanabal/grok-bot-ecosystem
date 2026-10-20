"""Provenance-tagged, tamper-evident, budgeted memory store (SQLite, stdlib only).

Every record carries: record_id, source_id, origin_url (byte-exact), source_ref, trust
(`untrusted` unless an operator says otherwise), content_sha256, created_at, expires_at,
and a keyed MAC chain: mac = HMAC-SHA256(key, canonical(fields + prev_mac)).

* Secrets are redacted from content BEFORE hashing/storing; a URL containing a secret
  shape is refused (it is never stored altered, never stored with the secret).
* Dedup: one live record per content_sha256; a duplicate write returns the existing record.
* Budgets: per-record bytes, total live bytes, live record count, TTL.
* On every read the content hash and MAC are re-verified; failing rows are excluded and
  reported. verify_chain() checks the whole chain (deleted or reordered rows break it).
* Expired records are tombstoned (content dropped, MAC kept) so the chain stays verifiable.
"""

from dataclasses import dataclass
import hashlib
import hmac
import re
import sqlite3
import threading
import time

from control_plane.audit import contains_secret, redact_text
from control_plane.canonical import canonical_json

TRUST_LEVELS = ("untrusted", "operator")
SOURCE_ID = re.compile(r"[a-z][a-z0-9_.:-]{0,79}")
_URL_OK = re.compile(r"[\x21-\x7e]{1,2048}")
GENESIS = "0" * 64


class MemoryError_(ValueError):
    """A write was refused; `code` is stable and secret-free."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MemoryBudget:
    max_record_bytes: int = 64 * 1024
    max_total_bytes: int = 16 * 1024 * 1024
    max_records: int = 10_000
    default_ttl_s: int = 30 * 86400
    max_ttl_s: int = 365 * 86400


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS records (
         seq INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL UNIQUE,
         source_id TEXT NOT NULL, origin_url TEXT NOT NULL, source_ref TEXT NOT NULL,
         trust TEXT NOT NULL, content TEXT, content_sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
         created_at REAL NOT NULL, expires_at REAL NOT NULL, purged INTEGER NOT NULL DEFAULT 0,
         prev_mac TEXT NOT NULL, mac TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS live_content ON records(content_sha256) WHERE purged = 0",
)
_MAC_FIELDS = ("seq", "record_id", "source_id", "origin_url", "source_ref", "trust", "content_sha256",
               "bytes", "created_at", "expires_at", "prev_mac")
_COLS = ("seq", "record_id", "source_id", "origin_url", "source_ref", "trust", "content", "content_sha256",
         "bytes", "created_at", "expires_at", "purged", "prev_mac", "mac")


def fts5_available() -> bool:
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return True
    except sqlite3.OperationalError:
        return False


class MemoryStore:
    def __init__(self, path: str, key: bytes, *, budget: MemoryBudget | None = None, clock=time.time) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("memory MAC key must be >= 32 bytes")
        self._key, self._clock = key, clock
        self.budget = budget or MemoryBudget()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA synchronous=FULL")
        for stmt in _SCHEMA:
            self._db.execute(stmt)
        self.search_mode = "fts5" if fts5_available() else "like"
        if self.search_mode == "fts5":
            self._db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(content, record_id UNINDEXED)")

    def close(self) -> None:
        self._db.close()

    def __del__(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ integrity
    def _mac(self, row: dict) -> str:
        return hmac.new(self._key, canonical_json({k: row[k] for k in _MAC_FIELDS}).encode(), hashlib.sha256).hexdigest()

    def _verify(self, row: dict) -> str | None:
        """None if the row is intact, else a stable reason."""
        if not hmac.compare_digest(self._mac(row), row["mac"]):
            return "mac_mismatch"
        if not row["purged"] and hashlib.sha256((row["content"] or "").encode()).hexdigest() != row["content_sha256"]:
            return "content_hash_mismatch"
        if row["trust"] not in TRUST_LEVELS:
            return "bad_trust"
        return None

    def verify_chain(self) -> tuple[bool, int, str]:
        with self._lock:
            rows = [dict(zip(_COLS, r)) for r in self._db.execute(f"SELECT {', '.join(_COLS)} FROM records ORDER BY seq")]
        prev, expected_seq = GENESIS, None
        for row in rows:
            if row["prev_mac"] != prev:
                return False, row["seq"], "chain_break"
            bad = self._verify(row)
            if bad:
                return False, row["seq"], bad
            prev = row["mac"]
        return True, len(rows), ""

    def anchor_rows(self) -> list[tuple[int, str]]:
        with self._lock:
            return [(r[0], r[1]) for r in self._db.execute("SELECT seq, mac FROM records ORDER BY seq")]

    def head(self) -> str:
        with self._lock:
            r = self._db.execute("SELECT mac FROM records ORDER BY seq DESC LIMIT 1").fetchone()
        return r[0] if r else GENESIS

    # ------------------------------------------------------------------ writes
    def put(self, content: str, *, source_id: str, origin_url: str, source_ref: str = "",
            trust: str = "untrusted", ttl_s: int | None = None) -> dict:
        if not isinstance(content, str) or not content:
            raise MemoryError_("empty_content")
        if len(content.encode()) > self.budget.max_record_bytes:
            raise MemoryError_("record_too_large")
        if trust not in TRUST_LEVELS:
            raise MemoryError_("bad_trust")
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise MemoryError_("bad_source_id")
        if not isinstance(origin_url, str) or not _URL_OK.fullmatch(origin_url):
            raise MemoryError_("bad_origin_url")
        if contains_secret(origin_url):
            raise MemoryError_("origin_url_contains_secret")
        if not isinstance(source_ref, str) or len(source_ref) > 200 or contains_secret(source_ref):
            raise MemoryError_("bad_source_ref")
        ttl = self.budget.default_ttl_s if ttl_s is None else int(ttl_s)
        if not (1 <= ttl <= self.budget.max_ttl_s):
            raise MemoryError_("bad_ttl")
        content, redactions = redact_text(content)
        sha = hashlib.sha256(content.encode()).hexdigest()
        nbytes = len(content.encode())
        now = self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            existing = self._db.execute("SELECT record_id FROM records WHERE content_sha256=? AND purged=0",
                                        (sha,)).fetchone()
            if existing is not None:
                return {"record_id": existing[0], "deduplicated": True, "redactions": redactions,
                        "content_sha256": sha}
            live, total = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM records WHERE purged=0").fetchone()
            if live + 1 > self.budget.max_records:
                raise MemoryError_("record_budget_exhausted")
            if total + nbytes > self.budget.max_total_bytes:
                raise MemoryError_("store_budget_exhausted")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                prev = self._db.execute("SELECT mac FROM records ORDER BY seq DESC LIMIT 1").fetchone()
                seq = (self._db.execute("SELECT COALESCE(MAX(seq), 0) FROM records").fetchone()[0]) + 1
                row = {"seq": seq, "record_id": f"mem:{sha[:32]}:{seq}", "source_id": source_id,
                       "origin_url": origin_url, "source_ref": source_ref, "trust": trust, "content": content,
                       "content_sha256": sha, "bytes": nbytes, "created_at": now, "expires_at": now + ttl,
                       "purged": 0, "prev_mac": prev[0] if prev else GENESIS}
                row["mac"] = self._mac(row)
                self._db.execute(f"INSERT INTO records ({', '.join(_COLS)}) VALUES ({', '.join('?' * len(_COLS))})",
                                 tuple(row[c] for c in _COLS))
                if self.search_mode == "fts5":
                    self._db.execute("INSERT INTO records_fts (content, record_id) VALUES (?, ?)",
                                     (content, row["record_id"]))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return {"record_id": row["record_id"], "deduplicated": False, "redactions": redactions,
                "content_sha256": sha, "mac": row["mac"], "seq": seq}

    def _purge_expired_locked(self, now: float) -> int:
        ids = [r[0] for r in self._db.execute("SELECT record_id FROM records WHERE purged=0 AND expires_at <= ?", (now,))]
        for rid in ids:
            self._db.execute("UPDATE records SET content=NULL, purged=1 WHERE record_id=?", (rid,))
            if self.search_mode == "fts5":
                self._db.execute("DELETE FROM records_fts WHERE record_id=?", (rid,))
        return len(ids)

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge_expired_locked(self._clock())

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _fts_query(query: str) -> str:
        """Reduce free text to quoted alphanumeric terms: FTS5 syntax can never be injected."""
        terms = re.findall(r"[A-Za-z0-9]{1,40}", query)[:10]
        return " ".join(f'"{t}"' for t in terms)

    def search(self, query: str, limit: int = 5) -> tuple[list[dict], list[dict]]:
        """Returns (intact live rows, excluded [{record_id, reason}])."""
        limit = max(1, min(int(limit), 20))
        now = self._clock()
        cols = ", ".join(f"r.{c}" for c in _COLS)
        with self._lock:
            if self.search_mode == "fts5":
                q = self._fts_query(query)
                if not q:
                    return [], []
                rows = self._db.execute(
                    f"SELECT {cols} FROM records_fts f JOIN records r ON r.record_id = f.record_id "
                    f"WHERE records_fts MATCH ? AND r.purged = 0 AND r.expires_at > ? ORDER BY rank LIMIT ?",
                    (q, now, limit * 4)).fetchall()
            else:
                terms = re.findall(r"[A-Za-z0-9]{1,40}", query)[:10]
                if not terms:
                    return [], []
                where = " AND ".join("r.content LIKE ? ESCAPE '\\'" for _ in terms)
                rows = self._db.execute(
                    f"SELECT {cols} FROM records r WHERE {where} AND r.purged = 0 AND r.expires_at > ? "
                    f"ORDER BY r.seq DESC LIMIT ?", (*[f"%{t}%" for t in terms], now, limit * 4)).fetchall()
        good, bad = [], []
        for r in rows:
            row = dict(zip(_COLS, r))
            reason = self._verify(row)
            if reason:
                bad.append({"record_id": row["record_id"], "reason": reason})
            elif len(good) < limit:
                good.append(row)
        return good, bad

    def get(self, record_id: str) -> tuple[dict | None, str | None]:
        with self._lock:
            r = self._db.execute(f"SELECT {', '.join(_COLS)} FROM records WHERE record_id=?", (record_id,)).fetchone()
        if r is None:
            return None, "not_found"
        row = dict(zip(_COLS, r))
        reason = self._verify(row)
        if reason:
            return None, reason
        if row["purged"] or row["expires_at"] <= self._clock():
            return None, "expired"
        return row, None

    def stats(self) -> dict:
        with self._lock:
            live, total = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM records WHERE purged=0").fetchone()
        return {"live_records": live, "live_bytes": total, "search_mode": self.search_mode}


def wrap(row: dict) -> dict:
    """The ONLY shape in which stored content leaves the memory layer: labelled untrusted
    data with provenance, never bare text."""
    return {"type": "untrusted_data", "trust": row["trust"],
            "provenance": {"record_id": row["record_id"], "source_id": row["source_id"],
                           "origin_url": row["origin_url"], "source_ref": row["source_ref"],
                           "content_sha256": row["content_sha256"], "created_at": row["created_at"],
                           "expires_at": row["expires_at"]},
            "content": row["content"]}
