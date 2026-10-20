"""Wiring for cycle-010 outreach tests: synthetic fixtures only (example.invalid), a Harness
control plane, an OutboxStore and a MockTransport. No real transport exists."""
import os

from tests.helpers import Harness  # noqa: I001 (puts src/ on sys.path first)
from outreach import MockTransport, OutboxStore, OutreachContext, SendCaps, build, register_outreach, send_request

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "outreach")
SOURCES = [("pipeline.csv", "pipeline", os.path.join(FIX, "pipeline.csv")),
           ("omega.csv", "omega", os.path.join(FIX, "omega.csv"))]
OUT_KEY = b"o" * 32
ACTOR = "agent.alpha"


class OutreachHarness:
    def __init__(self, *, live=True, transport=None, caps=SendCaps(), ctx_defaults=False):
        self.h = Harness()
        self.store = OutboxStore(os.path.join(self.h.dir.name, "outbox.sqlite"), OUT_KEY)
        self.transport = transport if transport is not None else MockTransport()
        if ctx_defaults:   # production defaults: DenyTransport + dry_run=True
            self.ctx = OutreachContext(store=self.store, audit=self.h.audit, clock=self.h.wall)
        else:
            self.ctx = OutreachContext(store=self.store, audit=self.h.audit, transport=self.transport,
                                       dry_run=not live, caps=caps, clock=self.h.wall)
        register_outreach(self.h.cp, self.ctx)
        for cap in ("cap.outreach.enqueue", "cap.outreach.send", "cap.outreach.outcome", "cap.outreach.confirm",
                    "cap.outreach.reply"):
            self.h.policy.grant(ACTOR, cap)

    def build(self, **kw):
        return build(self.h.cp, self.ctx, SOURCES, actor=ACTOR, **kw)

    def pending(self):
        return self.store.drafts("pending_approval")

    def token(self, draft, mode="live", attempt=1, **override):
        r = send_request(ACTOR, draft, mode, attempt)
        args = {**r["args"], **override}
        return self.h.approvals.issue(actor=ACTOR, capability="cap.outreach.send", operation_id=r["operation_id"],
                                      args=args, ttl_seconds=600, approver="zanabal")

    def send(self, draft, token=None, mode="live", attempt=1, **override):
        r = send_request(ACTOR, draft, mode, attempt, token)
        r["args"] = {**r["args"], **override}
        return self.h.cp.execute(r)

    def sent_count(self):
        return self.store.db.execute("SELECT COUNT(*) FROM sends").fetchone()[0]

    def close(self):
        self.store.close()
        self.h.close()
