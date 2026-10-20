"""Append-only, hash-chained JSONL audit log with secret redaction.

Each record carries seq, prev_hash and hash = SHA-256(prev_hash + canonical(record)).
Redaction runs before anything touches disk: sensitive keys are masked wholesale and
known credential shapes are masked inside any string value.
"""

import hashlib
import json
import os
import re
import threading
import time
from typing import Any

from .canonical import canonical_json

REDACTED = "[REDACTED]"
GENESIS = "0" * 64

_SENSITIVE_KEY = re.compile(
    r"(secret|token|passw(or)?d|api[_-]?key|authorization|cookie|credential|private[_-]?key|session)",
    re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-[A-Za-z0-9_-]{16,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----)")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if _SENSITIVE_KEY.search(str(k)) else redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(REDACTED, value)
    return value


def redact_text(value: str) -> tuple[str, int]:
    """Mask known credential shapes inside free text; returns (text, number_of_redactions)."""
    return _SECRET_VALUE.subn(REDACTED, value)


def contains_secret(value: str) -> bool:
    return _SECRET_VALUE.search(value) is not None


def _digest(prev_hash: str, record: dict) -> str:
    return hashlib.sha256((prev_hash + canonical_json(record)).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._seq, self._prev = 0, GENESIS
        self.listeners: list = []   # host-side observers, e.g. economics egress meter (cycle-009)
        if os.path.exists(path):
            ok, count, err = self.verify()
            if not ok:
                raise RuntimeError(f"audit log integrity failure: {err}")
            self._seq = count
            if count:
                with open(path, "rb") as fh:
                    last = fh.read().splitlines()[-1]
                self._prev = json.loads(last)["hash"]

    def append(self, event: str, **fields: Any) -> dict:
        with self._lock:
            record = {"seq": self._seq + 1, "ts": time.time(), "event": event,
                      "data": redact(fields), "prev_hash": self._prev}
            record["hash"] = _digest(self._prev, record)
            line = canonical_json(record) + "\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            self._seq, self._prev = record["seq"], record["hash"]
        for fn in self.listeners:
            fn(event, record["data"])
        return record

    def verify(self) -> tuple[bool, int, str]:
        return verify_chain(self.path)


def verify_chain(path: str) -> tuple[bool, int, str]:
    """Return (ok, record_count, error) for the audit log at `path`."""
    prev, n = GENESIS, 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for n, line in enumerate(fh, start=1):
                rec = json.loads(line)
                claimed = rec.pop("hash")
                if rec.get("seq") != n or rec.get("prev_hash") != prev:
                    return False, n, f"chain_break_at_seq_{n}"
                if _digest(prev, rec) != claimed:
                    return False, n, f"hash_mismatch_at_seq_{n}"
                prev = claimed
    except FileNotFoundError:
        return True, 0, ""
    except (ValueError, KeyError, TypeError, AttributeError):
        return False, n, f"malformed_record_at_line_{n}"
    return True, n, ""
