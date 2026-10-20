"""Governed ZAIPOS lead + outreach workflow (cycle-010, D-0026) with the two-phase Gmail handoff
transport (cycle-011, D-0028). No code in this package delivers mail."""

from .drafts import OPT_OUT_LINE, draft_hash, make_draft, render
from .handlers import (CONFIRM_CAP, ENQUEUE_CAP, OUTCOME_CAP, REPLY_CAP, SEND_CAP, DenyTransport, HandoffTransport,
                       MockTransport, OutreachContext, SendCaps, TransportDenied, classify_reply, register_outreach,
                       send_request)
from .issuer import GrantError, StandingIssuer
from .leads import ingest, norm_email, score
from .pipeline import KeyCustodyError, approval_key, build, export_pending, open_runtime
from .store import OUTCOMES, OutboxStore

__all__ = ["OPT_OUT_LINE", "draft_hash", "make_draft", "render", "CONFIRM_CAP", "ENQUEUE_CAP", "OUTCOME_CAP",
           "REPLY_CAP", "SEND_CAP", "DenyTransport", "HandoffTransport", "MockTransport", "OutreachContext", "SendCaps",
           "TransportDenied", "classify_reply", "register_outreach", "send_request", "GrantError", "StandingIssuer",
           "ingest", "norm_email", "score", "KeyCustodyError", "approval_key", "build", "export_pending",
           "open_runtime", "OUTCOMES", "OutboxStore"]
