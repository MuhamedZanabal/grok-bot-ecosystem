"""Standing-permission approval issuer (cycle-011, D-0028, S-0013).

A grant is the user's recorded standing permission: a reference, the day it was given, the
statement, the template it covers and a send ceiling. Grant rows are MAC'd with the approval key
(kept outside the state dir), so an outbox-key holder cannot forge one. For each draft the issuer
mints a normal ControlPlane approval token bound to actor, cap.outreach.send, the operation id and
the exact args {draft_id, draft_hash, recipient, channel, mode=handoff, grant_ref}; every token is
audited with its grant reference.
"""

import hashlib
import hmac
import re

from control_plane import ApprovalAuthority
from control_plane.canonical import canonical_json

from .handlers import SEND_CAP

TOKEN_TTL_S = 600
MAX_GRANT_SENDS = 50
_REF = re.compile(r"[a-z0-9][a-z0-9:._-]{2,79}")
_DAY = re.compile(r"20\d\d-[01]\d-[0-3]\d")
_GF = ("ref", "template_id", "granted_on", "statement", "max_sends", "recorded_at")


class GrantError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StandingIssuer:
    def __init__(self, key: bytes, store, audit, clock) -> None:
        self._auth, self._key = ApprovalAuthority(key, clock=clock), key
        self.store, self.audit, self.clock = store, audit, clock

    def __repr__(self) -> str:
        return "StandingIssuer(key=<redacted>)"

    def _mac(self, g: dict) -> str:
        return hmac.new(self._key, b"grant|" + canonical_json({k: g[k] for k in _GF}).encode(),
                        hashlib.sha256).hexdigest()

    def record(self, ref: str, *, granted_on: str, statement: str, max_sends: int, template_id: str,
               granted_by: str) -> dict:
        if not _REF.fullmatch(ref) or not _DAY.fullmatch(granted_on) or not statement.strip():
            raise GrantError("grant_malformed")
        if not 1 <= int(max_sends) <= MAX_GRANT_SENDS:
            raise GrantError("grant_max_sends_out_of_range")
        g = {"ref": ref, "template_id": template_id, "granted_on": granted_on, "statement": statement.strip(),
             "max_sends": int(max_sends), "recorded_at": self.clock()}
        with self.store.lock:
            if self.store.grant(ref) is not None:
                raise GrantError("grant_exists")
            self.store.db.execute("INSERT INTO grants VALUES (?,?,?,?,?,?,NULL,?)",
                                  tuple(g[k] for k in _GF) + (self._mac(g),))
        self.audit.append("outreach_standing_grant", grant_ref=ref, granted_on=granted_on, granted_by=granted_by,
                          template_id=template_id, max_sends=g["max_sends"],
                          statement_sha256=hashlib.sha256(g["statement"].encode()).hexdigest())
        return g

    def revoke(self, ref: str) -> bool:
        with self.store.lock:
            n = self.store.db.execute("UPDATE grants SET revoked_at=? WHERE ref=? AND revoked_at IS NULL",
                                      (self.clock(), ref)).rowcount
        if n:
            self.audit.append("outreach_grant_revoked", grant_ref=ref)
        return bool(n)

    def check(self, ref: str, template_id: str) -> dict:
        g = self.store.grant(ref)
        if g is None:
            raise GrantError("grant_missing")
        if not hmac.compare_digest(self._mac(g), g["mac"]):
            raise GrantError("grant_tampered")
        if g["revoked_at"] is not None:
            raise GrantError("grant_revoked")
        if g["template_id"] != template_id:
            raise GrantError("grant_scope")
        if self.store.grant_used(ref) >= g["max_sends"]:
            raise GrantError("grant_exhausted")
        return g

    def token(self, request: dict, draft: dict) -> str:
        a = request["args"]
        if request["capability"] != SEND_CAP or a.get("mode") != "handoff" or a.get("draft_id") != draft["draft_id"]:
            raise GrantError("grant_scope")
        self.check(a.get("grant_ref", ""), draft["template_id"])
        tok = self._auth.issue(actor=request["actor"], capability=SEND_CAP, operation_id=request["operation_id"],
                               args=a, ttl_seconds=TOKEN_TTL_S, approver=f"standing:{a['grant_ref']}")
        self.audit.append("outreach_approval_issued", grant_ref=a["grant_ref"], draft_id=a["draft_id"],
                          draft_hash=a["draft_hash"], operation_id=request["operation_id"])
        return tok
