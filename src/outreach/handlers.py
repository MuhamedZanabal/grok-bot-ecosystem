"""Outreach capabilities (TrustedHostHandler: repository code in the host, cycle-010, D-0026).

cap.outreach.enqueue  LOCAL_WRITE   draft -> queue as pending_approval (never sends)
cap.outreach.send     IRREVERSIBLE  risk 3: the ControlPlane demands an HMAC approval token bound
                                    to actor, capability, operation id and these exact args
                                    {draft_id, draft_hash, recipient, channel}. The handler then
                                    re-reads the MAC'd draft, recomputes its hash, re-checks
                                    suppression and caps, and only then calls the transport.
cap.outreach.outcome  LOCAL_WRITE   reply/outcome; revenue is not an accepted field
Transport defaults: DenyTransport and dry_run=True. No real transport exists in this repo.
"""

import time
from dataclasses import dataclass

from control_plane import Capability, HandlerFailure, RiskLevel, TrustedHostHandler

from .drafts import draft_hash
from .leads import norm_email
from .store import OUTCOMES

ENQUEUE_CAP, SEND_CAP, OUTCOME_CAP = "cap.outreach.enqueue", "cap.outreach.send", "cap.outreach.outcome"
_HEX = {"type": "string", "pattern": "[0-9a-f]{64}"}
_DID = {"type": "string", "pattern": "drf:[0-9a-f]{24}"}
_LID = {"type": "string", "pattern": "lead:[0-9a-f]{20}"}
_TXT = lambda n: {"type": "string", "minLength": 1, "maxLength": n}
SEND_SCHEMA = {"type": "object", "required": ["draft_id", "draft_hash", "recipient", "channel", "mode"],
               "additionalProperties": False,
               "properties": {"draft_id": _DID, "draft_hash": _HEX, "recipient": _TXT(254),
                              "channel": {"type": "string", "enum": ["email"]},
                              "mode": {"type": "string", "enum": ["dry_run", "live"]}}}
ENQUEUE_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["lead_id", "channel", "recipient", "subject", "body", "template_id",
                               "personalization", "meta"],
                  "properties": {"lead_id": _LID, "channel": {"type": "string", "enum": ["email"]},
                                 "recipient": _TXT(254), "subject": _TXT(200), "body": _TXT(4000),
                                 "template_id": _TXT(64), "personalization": {"type": "array", "maxItems": 8},
                                 "meta": {"type": "object", "additionalProperties": False,
                                          "required": ["score", "max", "evidence", "sources", "conflicts"],
                                          "properties": {"score": {"type": "integer"}, "max": {"type": "integer"},
                                                         "evidence": {"type": "array", "maxItems": 16},
                                                         "sources": {"type": "array", "maxItems": 8},
                                                         "conflicts": {"type": "integer"}}}}}
OUTCOME_SCHEMA = {"type": "object", "required": ["lead_id", "outcome", "evidence"], "additionalProperties": False,
                  "properties": {"lead_id": _LID, "outcome": {"type": "string", "enum": list(OUTCOMES[1:])},
                                 "evidence": _TXT(300)}}


class TransportDenied(Exception):
    pass


class DenyTransport:
    name = "deny"

    def send(self, channel, recipient, subject, body) -> str:
        raise TransportDenied("transport_disabled")


class MockTransport:
    """Test double: records messages in memory. Delivers nothing anywhere."""
    name = "mock"

    def __init__(self) -> None:
        self.sent = []

    def send(self, channel, recipient, subject, body) -> str:
        self.sent.append((channel, recipient, subject, body))
        return f"mock-{len(self.sent)}"


@dataclass(frozen=True)
class SendCaps:
    per_day: int = 4                 # revops_report.md: <= 4 per day
    per_recipient_total: int = 2     # stop after 2 touches
    per_recipient_day: int = 1


class _Host(TrustedHostHandler):
    __slots__ = ("_c",)

    def __init__(self, ctx) -> None:
        object.__setattr__(self, "_c", ctx)

    def __setattr__(self, name, value) -> None:
        raise AttributeError("immutable")


@dataclass
class OutreachContext:
    store: object
    audit: object
    transport: object = DenyTransport()
    dry_run: bool = True
    caps: SendCaps = SendCaps()
    clock: object = time.time


def draft_id_for(lead_id: str, template_id: str, h: str) -> str:
    import hashlib
    return "drf:" + hashlib.sha256(f"{lead_id}|{template_id}|{h}".encode()).hexdigest()[:24]


class EnqueueHandler(_Host):
    __slots__ = ()

    def __call__(self, a):
        c = self._c
        if not norm_email(a["recipient"]):
            raise HandlerFailure("outreach:bad_recipient")
        if c.store.suppressed(a["recipient"]):
            raise HandlerFailure("outreach:suppressed")
        h = draft_hash(a["channel"], a["recipient"], a["subject"], a["body"])
        d = {**a, "draft_hash": h, "draft_id": draft_id_for(a["lead_id"], a["template_id"], h)}
        row, created = c.store.put_draft(d, c.clock())
        if created:
            c.audit.append("outreach_enqueued", draft_id=row["draft_id"], draft_hash=h, lead_id=a["lead_id"])
        return {"draft_id": row["draft_id"], "draft_hash": row["draft_hash"], "state": row["state"], "created": created}


class SendHandler(_Host):
    __slots__ = ()

    def __call__(self, a):
        c = self._c
        d = c.store.draft(a["draft_id"])
        if d is None:
            raise HandlerFailure("outreach:no_such_draft")
        if not d["intact"]:
            raise HandlerFailure("outreach:draft_tampered")
        if draft_hash(d["channel"], d["recipient"], d["subject"], d["body"]) != d["draft_hash"] \
                or d["draft_hash"] != a["draft_hash"]:
            raise HandlerFailure("outreach:draft_hash_mismatch")
        if (d["recipient"], d["channel"]) != (a["recipient"], a["channel"]):
            raise HandlerFailure("outreach:recipient_or_channel_mismatch")
        if d["state"] != "pending_approval":
            raise HandlerFailure(f"outreach:not_pending:{d['state']}")
        if c.store.suppressed(d["recipient"]):
            raise HandlerFailure("outreach:suppressed")
        now = c.clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        with c.store.lock:
            db = c.store.db
            db.execute("BEGIN IMMEDIATE")
            try:
                n = c.store.counts(d["recipient_norm"], d["lead_id"], day)
                for used, cap, code in ((n["day"], c.caps.per_day, "day"),
                                        (n["recipient_total"], c.caps.per_recipient_total, "recipient_total"),
                                        (n["recipient_day"], c.caps.per_recipient_day, "recipient_day")):
                    if used >= cap:
                        raise HandlerFailure(f"outreach:cap:{code}")
                if c.dry_run or a["mode"] == "dry_run":   # deployment dry-run forces dry even for "live"
                    db.execute("ROLLBACK")
                    c.audit.append("outreach_dry_run", draft_id=d["draft_id"], draft_hash=d["draft_hash"],
                                   forced=a["mode"] == "live")
                    return {"dry_run": True, "would_send": True, "draft_id": d["draft_id"], "forced": a["mode"] == "live"}
                try:
                    mid = c.transport.send(d["channel"], d["recipient"], d["subject"], d["body"])
                except TransportDenied as exc:
                    raise HandlerFailure(f"outreach:{exc}") from None
                db.execute("INSERT INTO sends VALUES (?,?,?,?,?,?,?)",
                           (d["draft_id"], d["lead_id"], d["recipient_norm"], day, now, c.transport.name, str(mid)))
                db.execute("UPDATE drafts SET state='sent' WHERE draft_id=? AND state='pending_approval'",
                           (d["draft_id"],))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        c.audit.append("outreach_sent", draft_id=d["draft_id"], draft_hash=d["draft_hash"], transport=c.transport.name)
        return {"sent": True, "draft_id": d["draft_id"], "message_id": str(mid)}


class OutcomeHandler(_Host):
    __slots__ = ()

    def __call__(self, a):
        c = self._c
        now = c.clock()
        with c.store.lock:
            c.store.db.execute("INSERT INTO outcomes VALUES (?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET "
                               "outcome=excluded.outcome, evidence=excluded.evidence, updated_at=excluded.updated_at",
                               (a["lead_id"], a["outcome"], a["evidence"], now))
        if a["outcome"] == "opted_out":
            for (r,) in c.store.db.execute("SELECT DISTINCT recipient FROM drafts WHERE lead_id=?", (a["lead_id"],)):
                c.store.suppress(r, "opted_out", "tracker", now)
            c.store.db.execute("UPDATE drafts SET state='rejected', reason='opted_out' "
                               "WHERE lead_id=? AND state='pending_approval'", (a["lead_id"],))
        c.audit.append("outreach_outcome", lead_id=a["lead_id"], outcome=a["outcome"])
        return c.store.outcome(a["lead_id"])


def register_outreach(cp, ctx: OutreachContext) -> None:
    for cid, risk, desc, schema, h in (
            (ENQUEUE_CAP, RiskLevel.LOCAL_WRITE, "queue an outreach draft as pending_approval", ENQUEUE_SCHEMA,
             EnqueueHandler),
            (SEND_CAP, RiskLevel.IRREVERSIBLE, "send one approved outreach draft", SEND_SCHEMA, SendHandler),
            (OUTCOME_CAP, RiskLevel.LOCAL_WRITE, "record a reply/outcome for a lead", OUTCOME_SCHEMA, OutcomeHandler)):
        if cp.policy.capability(cid) is None:
            cp.policy.register(Capability(cid, risk, desc, schema))
        cp.bind(cid, h(ctx))


def send_request(actor: str, draft: dict, mode: str = "live", attempt: int = 1, token: str | None = None) -> dict:
    """The exact request an approver signs: mode and recipient are in the args, the attempt in the
    operation id. At-most-once delivery per draft is enforced by sends(draft_id) PRIMARY KEY and the
    pending_approval state, so a new attempt never re-sends a sent draft."""
    r = {"operation_id": f"outreach:{mode}:{draft['draft_id'][4:]}:{int(attempt)}", "actor": actor,
         "capability": SEND_CAP,
         "args": {"draft_id": draft["draft_id"], "draft_hash": draft["draft_hash"], "recipient": draft["recipient"],
                  "channel": draft["channel"], "mode": mode}}
    if token is not None:
        r["approval_token"] = token
    return r
