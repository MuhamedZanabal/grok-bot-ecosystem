"""Approval tokens bound to (actor, capability, operation_id, SHA-256(args), expiry).

Token = base64url(canonical payload) + "." + hex(HMAC-SHA256(key, payload)).
Because the token binds the operation_id and the idempotency ledger executes an
operation_id at most once, an approval authorizes at most one execution.
"""

import base64
import binascii
import hmac
import hashlib
import json
import secrets
import time
from typing import Callable

from .canonical import args_sha256, canonical_json

MAX_TTL_SECONDS = 3600
MIN_KEY_BYTES = 32


class ApprovalError(Exception):
    """Token rejected. `code` is a stable, deterministic reason string."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class ApprovalAuthority:
    def __init__(self, key: bytes, clock: Callable[[], float] = time.time) -> None:
        if not isinstance(key, bytes) or len(key) < MIN_KEY_BYTES:
            raise ValueError(f"approval key must be >= {MIN_KEY_BYTES} bytes")
        self._key = key
        self._clock = clock

    def __repr__(self) -> str:  # never expose the key
        return "ApprovalAuthority(key=<redacted>)"

    def _sign(self, payload: bytes) -> str:
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def issue(self, *, actor: str, capability: str, operation_id: str, args: dict,
              ttl_seconds: int, approver: str) -> str:
        if not 0 < ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError(f"ttl must be in (0, {MAX_TTL_SECONDS}]")
        now = int(self._clock())
        payload = canonical_json({
            "v": 1, "token_id": secrets.token_hex(8), "approver": approver,
            "actor": actor, "capability": capability, "operation_id": operation_id,
            "args_sha256": args_sha256(args), "issued_at": now,
            "expires_at": now + ttl_seconds,
        }).encode("utf-8")
        return f"{_b64e(payload)}.{self._sign(payload)}"

    def verify(self, token: object, *, actor: str, capability: str,
               operation_id: str, args: dict) -> dict:
        if not isinstance(token, str) or token.count(".") != 1 or len(token) > 4096:
            raise ApprovalError("approval_malformed")
        body, sig = token.split(".")
        try:
            payload = _b64d(body)
        except (binascii.Error, ValueError):
            raise ApprovalError("approval_malformed") from None
        if not hmac.compare_digest(self._sign(payload), sig):
            raise ApprovalError("approval_bad_signature")
        try:
            claims = json.loads(payload)
        except ValueError:
            raise ApprovalError("approval_malformed") from None
        if not isinstance(claims, dict) or claims.get("v") != 1:
            raise ApprovalError("approval_malformed")
        if self._clock() >= claims.get("expires_at", 0):
            raise ApprovalError("approval_expired")
        if (claims.get("actor") != actor or claims.get("capability") != capability
                or claims.get("operation_id") != operation_id):
            raise ApprovalError("approval_wrong_scope")
        if not hmac.compare_digest(str(claims.get("args_sha256")), args_sha256(args)):
            raise ApprovalError("approval_args_mismatch")
        return claims
