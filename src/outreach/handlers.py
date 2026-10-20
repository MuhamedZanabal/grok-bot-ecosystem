"""Outreach capabilities (TrustedHostHandler: repository code in the host, cycle-010, D-0026).

cap.outreach.enqueue  LOCAL_WRITE   draft -> queue as pending_approval (never sends)
cap.outreach.send     IRREVERSIBLE  risk 3: the ControlPlane demands an HMAC approval token bound
                                    to actor, capability, operation id and these exact args
                                    {draft_id, draft_hash, recipient, channel}. The handler then
                                    re-reads the MAC'd draft, recomputes its hash, re-checks
                                    suppression and caps, and only then calls the transport.
cap.outreach.outcome  LOCAL_WRITE   reply/outcome; revenue is not an accepted field
cap.outreach.confirm  LOCAL_WRITE   cycle-011: record the external result of a handoff reservation
cap.outreach.reply    LOCAL_WRITE   cycle-011: ingest a reply; opt-out keywords suppress the sender
Transport defaults: DenyTransport and dry_run=True. No transport in this repo delivers mail. Mode
"handoff" (cycle-011, D-0028) needs HandoffTransport + dry_run=False: the handler reserves the send
(caps count reservations) and an external sender (the user's Gmail) delivers the manifest entry.
"""

import hashlib
import re
import secrets
import time
from dataclasses import dataclass

from control_plane import Capability, HandlerFailure, RiskLevel, TrustedHostHandler

from .drafts import OPT_OUT_LINE, draft_hash
from .leads import norm_email
from .store import OUTCOMES

ENQUEUE_CAP, SEND_CAP, OUTCOME_CAP = "cap.outreach.enqueue", "cap.outreach.send", "cap.outreach.outcome"
CONFIRM_CAP, REPLY_CAP = "cap.outreach.confirm", "cap.outreach.reply"
RESERVATION_TTL_S = 12 * 3600
_GREF = {"type": "string", "pattern": "[a-z0-9][a-z0-9:._-]{2,79}"}
_RID = {"type": "string", "pattern": "rsv:[0-9a-f]{24}"}
_MID = {"type": "string", "pattern": "[A-Za-z0-9_.:@<>+=-]{1,256}"}
_HEX = {"type": "string", "pattern": "[0-9a-f]{64}"}
_DID = {"type": "string", "pattern": "drf:[0-9a-f]{24}"}
_LID = {"type": "string", "pattern": "lead:[0-9a-f]{20}"}
_TXT = lambda n: {"type": "string", "minLength": 1, "maxLength": n}
SEND_SCHEMA = {"type": "object", "required": ["draft_id", "draft_hash", "recipient", "channel", "mode"],
               "additionalProperties": False,
               "properties": {"draft_id": _DID, "draft_hash": _HEX, "recipient": _TXT(254),
                              "channel": {"type": "string", "enum": ["email"]},
                              "mode": {"type": "string", "enum": ["dry_run", "live", "handoff"]},
                              "grant_ref": _GREF}}
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
CONFIRM_SCHEMA = {"type": "object", "required": ["reservation_id", "result", "body_sha256"], "additionalProperties": False,
                  "properties": {"reservation_id": _RID, "result": {"type": "string", "enum": ["sent", "failed"]},
                                 "message_id": _MID, "detail": _TXT(300),
                                 "body_sha256": {"type": "string", "pattern": "[0-9a-f]{64}|missing"}}}
REPLY_SCHEMA = {"type": "object", "required": ["sender", "text", "message_id"], "additionalProperties": False,
                "properties": {"sender": _TXT(254), "text": {"type": "string", "maxLength": 20000}, "message_id": _MID}}
OPT_OUT = re.compile(r"\b(stop|unsubscribe|not interested|remove me|opt[\s-]?out|do not (?:contact|email))\b", re.I)
_QUOTE_HDR = re.compile(r"^(on .{0,200} wrote:|-+ ?original message ?-+|from: .*)$", re.I)


def classify_reply(text: str) -> str | None:
    """Opt-out keyword in the reply's own lines (quoted lines and the quoted original, which contains
    our own 'reply "stop"' line, are ignored). Returns the keyword or None."""
    own = []
    for line in text.splitlines():
        if _QUOTE_HDR.match(line.strip()):
            break
        if not line.lstrip().startswith(">"):
            own.append(line)
    m = OPT_OUT.search("\n".join(own))
    return m.group(1).lower() if m else None


class TransportDenied(Exception):
    pass


class DenyTransport:
    name = "deny"

    def send(self, channel, recipient, subject, body) -> str:
        raise TransportDenied("transport_disabled")


class HandoffTransport:
    """cycle-011: delivery happens outside this process (the user's Gmail via the agent's connector).
    `send` can never deliver; the send handler reserves instead and emits a manifest entry."""
    name, handoff = "gmail_handoff", True

    def send(self, channel, recipient, subject, body) -> str:
        raise TransportDenied("handoff_only")


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
        if OPT_OUT_LINE not in a["body"]:
            raise HandlerFailure("outreach:no_opt_out_line")
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
        if OPT_OUT_LINE not in d["body"]:
            raise HandlerFailure("outreach:no_opt_out_line")
        g = c.store.grant(a["grant_ref"]) if "grant_ref" in a else None
        if "grant_ref" in a and (g is None or g["revoked_at"] is not None or g["template_id"] != d["template_id"]):
            raise HandlerFailure("outreach:grant_inactive")
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
                if a["mode"] == "handoff":
                    if not getattr(c.transport, "handoff", False):
                        raise HandlerFailure("outreach:handoff_not_configured")
                    rid = "rsv:" + secrets.token_hex(12)     # unguessable; unknown to any other outbox
                    db.execute("INSERT INTO reservations (reservation_id, draft_id, draft_hash, lead_id, recipient_norm, "
                               "day, reserved_at, expires_at, grant_ref, body_sha256, state, updated_at) VALUES "
                               "(?,?,?,?,?,?,?,?,?,?,'reserved',?)",
                               (rid, d["draft_id"], d["draft_hash"], d["lead_id"], d["recipient_norm"], day, now,
                                now + RESERVATION_TTL_S, a.get("grant_ref", ""),
                                hashlib.sha256(d["body"].encode()).hexdigest(), now))
                    db.execute("UPDATE drafts SET state='reserved' WHERE draft_id=? AND state='pending_approval'",
                               (d["draft_id"],))
                    db.execute("COMMIT")
                    c.audit.append("outreach_reserved", draft_id=d["draft_id"], draft_hash=d["draft_hash"],
                                   reservation_id=rid, grant_ref=a.get("grant_ref", ""))
                    return {"reserved": True, "reservation_id": rid, "draft_id": d["draft_id"],
                            "expires_at": now + RESERVATION_TTL_S}
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


def _set_outcome(c, lead_id: str, outcome: str, evidence: str, now: float) -> None:
    with c.store.lock:
        c.store.db.execute("INSERT INTO outcomes VALUES (?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET "
                           "outcome=excluded.outcome, evidence=excluded.evidence, updated_at=excluded.updated_at",
                           (lead_id, outcome, evidence, now))
    if outcome == "opted_out":
        for (r,) in c.store.db.execute("SELECT DISTINCT recipient FROM drafts WHERE lead_id=?", (lead_id,)).fetchall():
            c.store.suppress(r, "opted_out", "tracker", now)
        c.store.db.execute("UPDATE drafts SET state='rejected', reason='opted_out' "
                           "WHERE lead_id=? AND state='pending_approval'", (lead_id,))
    c.audit.append("outreach_outcome", lead_id=lead_id, outcome=outcome)


class OutcomeHandler(_Host):
    __slots__ = ()

    def __call__(self, a):
        _set_outcome(self._c, a["lead_id"], a["outcome"], a["evidence"], self._c.clock())
        return self._c.store.outcome(a["lead_id"])


class ConfirmHandler(_Host):
    """Record the external result of a reservation. Unknown reservation -> refused. A send is
    recorded even when anomalous (late, after cancel, body changed) because it happened; the
    anomalies are returned and audited. A second confirmation never adds a second send row."""
    __slots__ = ()

    def __call__(self, a):
        c, now = self._c, self._c.clock()
        with c.store.lock:
            c.store.sweep(now)
            r = c.store.reservation(a["reservation_id"])
            if r is None:
                raise HandlerFailure("outreach:unknown_reservation")
            if r["state"] in ("sent", "failed"):
                raise HandlerFailure(f"outreach:already_{r['state']}")
            d = c.store.draft(r["draft_id"])
            anomalies = [k for k, bad in (
                ("late_in_doubt", r["state"] == "in_doubt"), ("after_cancel", r["state"] == "cancelled"),
                ("body_file_changed", a["body_sha256"] != r["body_sha256"]),
                ("draft_changed", d is None or not d["intact"] or d["draft_hash"] != r["draft_hash"])) if bad]
            db = c.store.db
            db.execute("BEGIN IMMEDIATE")
            try:
                if a["result"] == "sent":
                    if "message_id" not in a:
                        raise HandlerFailure("outreach:message_id_required")
                    if db.execute("SELECT 1 FROM reservations WHERE message_id=?", (a["message_id"],)).fetchone():
                        raise HandlerFailure("outreach:message_id_reused")
                    db.execute("INSERT INTO sends VALUES (?,?,?,?,?,?,?)", (r["draft_id"], r["lead_id"],
                               r["recipient_norm"], r["day"], now, "gmail_handoff", a["message_id"]))
                db.execute("UPDATE reservations SET state=?, message_id=?, detail=?, updated_at=? WHERE reservation_id=?",
                           (a["result"], a.get("message_id"), a.get("detail", ",".join(anomalies)), now, r["reservation_id"]))
                db.execute("UPDATE drafts SET state=? WHERE draft_id=?", (a["result"], r["draft_id"]))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        c.audit.append("outreach_send_recorded", reservation_id=r["reservation_id"], draft_id=r["draft_id"],
                       result=a["result"], anomalies=anomalies)
        return {"recorded": a["result"], "reservation_id": r["reservation_id"], "anomalies": anomalies}


class ReplyHandler(_Host):
    """Ingest an untrusted reply. The text is only classified, never stored or executed."""
    __slots__ = ()

    def __call__(self, a):
        c, now = self._c, self._c.clock()
        n = norm_email(a["sender"])
        if not n:
            raise HandlerFailure("outreach:bad_sender")
        kw = classify_reply(a["text"])
        if kw:
            c.store.suppress(a["sender"], "opted_out", f"reply:{a['message_id']}", now)
        leads = [x for (x,) in c.store.db.execute("SELECT DISTINCT lead_id FROM drafts WHERE recipient_norm=?", (n,))]
        for lead in leads:
            _set_outcome(c, lead, "opted_out" if kw else "replied", f"reply:{a['message_id']}:{kw or 'no_opt_out'}", now)
        c.audit.append("outreach_reply", opt_out=bool(kw), keyword=kw or "", leads=len(leads))
        return {"opt_out": bool(kw), "keyword": kw, "leads": len(leads), "suppressed": bool(c.store.suppressed(n))}


def register_outreach(cp, ctx: OutreachContext) -> None:
    for cid, risk, desc, schema, h in (
            (ENQUEUE_CAP, RiskLevel.LOCAL_WRITE, "queue an outreach draft as pending_approval", ENQUEUE_SCHEMA,
             EnqueueHandler),
            (SEND_CAP, RiskLevel.IRREVERSIBLE, "send one approved outreach draft", SEND_SCHEMA, SendHandler),
            (OUTCOME_CAP, RiskLevel.LOCAL_WRITE, "record a reply/outcome for a lead", OUTCOME_SCHEMA, OutcomeHandler),
            (CONFIRM_CAP, RiskLevel.LOCAL_WRITE, "record the external result of a handoff reservation",
             CONFIRM_SCHEMA, ConfirmHandler),
            (REPLY_CAP, RiskLevel.LOCAL_WRITE, "ingest a reply; opt-out suppresses the sender", REPLY_SCHEMA,
             ReplyHandler)):
        if cp.policy.capability(cid) is None:
            cp.policy.register(Capability(cid, risk, desc, schema))
        cp.bind(cid, h(ctx))


def send_request(actor: str, draft: dict, mode: str = "live", attempt: int = 1, token: str | None = None,
                 grant_ref: str | None = None) -> dict:
    """The exact request an approver signs: mode and recipient are in the args, the attempt in the
    operation id. At-most-once delivery per draft is enforced by sends(draft_id) PRIMARY KEY and the
    pending_approval state, so a new attempt never re-sends a sent draft."""
    r = {"operation_id": f"outreach:{mode}:{draft['draft_id'][4:]}:{int(attempt)}", "actor": actor,
         "capability": SEND_CAP,
         "args": {"draft_id": draft["draft_id"], "draft_hash": draft["draft_hash"], "recipient": draft["recipient"],
                  "channel": draft["channel"], "mode": mode}}
    if grant_ref is not None:
        r["args"]["grant_ref"] = grant_ref
    if token is not None:
        r["approval_token"] = token
    return r
