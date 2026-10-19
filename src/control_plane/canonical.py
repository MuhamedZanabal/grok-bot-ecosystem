"""Canonical JSON encoding and argument hashing.

Approvals and idempotency both bind to the SHA-256 of the canonical encoding, so the
encoding must be deterministic: sorted keys, no whitespace, UTF-8, NaN/Infinity refused.
"""

import hashlib
import json
from typing import Any


class CanonicalizationError(ValueError):
    """Raised when a value cannot be canonically encoded (non-JSON type, NaN, ...)."""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CanonicalizationError(f"not canonically encodable: {type(exc).__name__}") from exc


def args_sha256(args: Any) -> str:
    return hashlib.sha256(canonical_json(args).encode("utf-8")).hexdigest()
