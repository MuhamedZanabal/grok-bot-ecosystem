"""Adversarial outreach cases (cycle-010, D-0026). A violation is any message reaching the
transport without a valid approval for that exact draft/recipient/channel/mode, any delivery
after tampering, suppression or opt-out, any cap exceeded, a double send, a lead field acting
as an instruction or appearing unvetted in a message, or a revenue number accepted.

Success metric: violations == 0.
"""
import os
import sys
import unittest

from tests.outreach_helpers import ACTOR, OUT_KEY, OutreachHarness
from control_plane import Status
from outreach import OutboxStore, SendCaps, draft_hash
from scheduler import DefinitionError, JobStore, Scheduler

DAY = 86400


def _sent(o):
    return len(o.transport.sent)


def no_token(o):
    o.build()
    out = o.send(o.pending()[0])
    return [] if out.status is Status.APPROVAL_REQUIRED and _sent(o) == 0 else [f"no-token: {out.status}"]


def token_for_other_draft(o):
    o.build()
    a, b = o.pending()[:2]
    out = o.send(b, o.token(a))
    return [] if out.status is Status.APPROVAL_INVALID and _sent(o) == 0 else [f"cross-draft token: {out.status}"]


def token_other_recipient_or_channel(o):
    o.build()
    d = o.pending()[0]
    v = []
    tok = o.token(d)
    if o.send(d, tok, recipient="attacker@evil.example.invalid").status is not Status.APPROVAL_INVALID:
        v.append("token reused for another recipient")
    # even a validly signed token for a wrong recipient is refused by the handler
    bad = o.token(d, attempt=2, recipient="attacker@evil.example.invalid")
    out = o.send(d, bad, attempt=2, recipient="attacker@evil.example.invalid")
    if out.status is not Status.FAILED or "recipient_or_channel_mismatch" not in out.reason:
        v.append(f"handler accepted signed wrong recipient: {out.reason}")
    if o.send(d, o.token(d, mode="dry_run"), mode="live").status is not Status.APPROVAL_INVALID:
        v.append("dry-run token authorized a live send")
    if o.send(d, tok, channel="sms").status is not Status.REJECTED:
        v.append("unknown channel not rejected by schema")
    return v + ([f"transport called {_sent(o)}"] if _sent(o) else [])


def tamper_after_approval(o):
    o.build()
    a, b = o.pending()[:2]
    ta, tb = o.token(a), o.token(b)
    o.store.db.execute("UPDATE drafts SET body=body || 'P.S. wire BHD 500 to IBAN X' WHERE draft_id=?", (a["draft_id"],))
    # a key holder re-MACs an edited draft and updates its hash: the token still binds the old hash
    rogue = OutboxStore(o.store.db.execute("PRAGMA database_list").fetchone()[2], OUT_KEY)
    row = rogue.draft(b["draft_id"])
    body = row["body"] + "edited"
    h = draft_hash(row["channel"], row["recipient"], row["subject"], body)
    row.update(body=body, draft_hash=h)
    o.store.db.execute("UPDATE drafts SET body=?, draft_hash=?, mac=? WHERE draft_id=?",
                       (body, h, rogue._mac(row), b["draft_id"]))
    rogue.close()
    oa, ob = o.send(a, ta), o.send(b, tb)
    v = [] if oa.reason == "worker:outreach:draft_tampered" else [f"edited draft: {oa.reason}"]
    v += [] if ob.reason == "worker:outreach:draft_hash_mismatch" else [f"re-MAC'd draft: {ob.reason}"]
    return v + ([f"transport called {_sent(o)}"] if _sent(o) else [])


def suppressed_recipient(o):
    o.build()
    d = o.pending()[0]
    tok = o.token(d)
    local, dom = d["recipient"].split("@")
    o.store.suppress(f"  {local.upper()}+anything@{dom.upper()}. ", "operator", "test", 0)   # variant spelling
    out = o.send(d, tok)
    return [] if out.reason == "worker:outreach:suppressed" and _sent(o) == 0 else [f"suppressed sent: {out.reason}"]


def opt_out_after_approval(o):
    o.build()
    d = o.pending()[0]
    tok = o.token(d)
    o.h.cp.execute({"operation_id": "outcome-opt-1", "actor": ACTOR, "capability": "cap.outreach.outcome",
                    "args": {"lead_id": d["lead_id"], "outcome": "opted_out", "evidence": "said stop"}})
    out = o.send(d, tok)
    return [] if out.status is Status.FAILED and _sent(o) == 0 else [f"opted-out sent: {out.reason}"]


def day_cap_split(o):
    o.build()
    sent = sum(o.send(d, o.token(d)).status is Status.EXECUTED for d in o.pending())  # 8 approved drafts
    v = [] if sent == 4 and _sent(o) == 4 else [f"day cap: {sent} sent"]
    o.h.wall.t += DAY
    rest = [d for d in o.pending()]
    sent2 = sum(o.send(d, o.token(d, attempt=2), attempt=2).status is Status.EXECUTED for d in rest)
    return v + ([] if sent2 == 4 else [f"next day: {sent2}"])


def recipient_cap_split(o):
    o.build()
    d = o.pending()[0]
    # attacker queues more drafts to the same mailbox under variant spellings and other templates
    local, dom = d["recipient"].split("@")
    variants = [d["recipient"], f"{local.upper()}@{dom}", f"{local}+x@{dom}", f"{local}+y@{dom.upper()}"]
    ids = []
    for i, rcpt in enumerate(variants):
        args = {k: (rcpt if k == "recipient" else d[k]) for k in ("lead_id", "channel", "recipient", "subject",
                                                                  "body", "template_id")}
        args.update(template_id=f"variant_{i}", personalization=d["personalization"], meta=d["meta"],
                    lead_id=f"lead:{i:020x}")   # posing as different leads
        out = o.h.cp.execute({"operation_id": f"enq-variant-{i}", "actor": ACTOR, "capability": "cap.outreach.enqueue",
                              "args": args})
        ids.append(out.result["draft_id"])
    results = []
    for day in range(4):
        for j, i in enumerate(ids):
            dd = o.store.draft(i)
            results.append(o.send(dd, o.token(dd, attempt=day + 1), attempt=day + 1).status is Status.EXECUTED)
        o.h.wall.t += DAY
    n = sum(results)
    v = [] if sum(results[:len(ids)]) == 1 else [f"recipient day cap: {sum(results[:len(ids)])} on day 1"]
    return v + ([] if n == 2 and _sent(o) == 2 else [f"recipient total cap: {n} sends to one mailbox"])


def double_send(o):
    o.build()
    d = o.pending()[0]
    tok = o.token(d)
    first, replay = o.send(d, tok), o.send(d, tok)
    again = o.send(d, o.token(d, attempt=2), attempt=2)
    v = [] if (first.status, replay.status) == (Status.EXECUTED, Status.REPLAYED) else [f"{first.status} {replay.status}"]
    v += [] if again.reason == "worker:outreach:not_pending:sent" else [f"second attempt: {again.reason}"]
    o.build()                                               # re-ingest must not reset a sent draft
    args = {k: d[k] for k in ("lead_id", "channel", "recipient", "subject", "body", "template_id",
                              "personalization", "meta")}
    o.h.cp.execute({"operation_id": "enq-again-0001", "actor": ACTOR, "capability": "cap.outreach.enqueue",
                    "args": args})                          # same content under a fresh operation id
    if o.store.draft(d["draft_id"])["state"] != "sent":
        v.append("rebuild reset a sent draft to pending")
    try:
        o.store.db.execute("INSERT INTO sends VALUES (?,?,?,?,?,?,?)", (d["draft_id"], "x", "y", "z", 0, "t", "m"))
        v.append("sends table accepted a second row for one draft")
    except Exception:
        pass
    return v + ([] if _sent(o) == 1 else [f"transport called {_sent(o)}"])


def dry_run_mode_request(o):
    o.build()
    d = o.pending()[0]
    out = o.send(d, o.token(d, mode="dry_run"), mode="dry_run")
    v = [] if out.status is Status.EXECUTED and out.result.get("dry_run") else [f"dry-run request: {out}"]
    return v + ([f"dry-run request reached transport {_sent(o)}"] if _sent(o) else [])


def unsafe_charset_names(o):
    from outreach.leads import _hold
    v = []
    for name in ("Pipe|Cafe", "Eq=Cafe", "Star*Bar", "Semi;colon Grill", "Tilde~Place", "Caret^Cafe"):
        if _hold({"name": name}) != "unsafe_field:name":
            v.append(f"not held: {name!r}")
    for name in ("Café Ñ Co.", "مطعم القاهرة", "Arte de Café (Adliya)", "Jasmi's #2"):
        if _hold({"name": name}) is not None:
            v.append(f"wrongly held: {name!r}")
    return v


def injection_in_lead_fields(o):
    st = o.build()
    v = [] if st["held"] == 2 else [f"held={st['held']}"]
    with open(o.h.audit_path) as fh:
        begins = [r for r in fh if '"event":"execute_begin"' in r]
    if any("cap.outreach.enqueue" not in r for r in begins):
        v.append("build invoked a capability other than enqueue")
    for d in o.store.drafts():
        low = (d["subject"] + d["body"]).lower()
        for bad in ("ignore previous", "system:", "assistant", "<script", "attacker@", "bcc:", "cap.outreach"):
            if bad in low:
                v.append(f"injected text in draft {d['draft_id']}: {bad}")
        if any(ch in d["body"] for ch in "\u202e\u200b"):
            v.append("bidi/zero-width char in draft")
    return v + ([f"transport called {_sent(o)}"] if _sent(o) else [])


def revenue_forged(o):
    o.build()
    d = o.pending()[0]
    v = []
    out = o.h.cp.execute({"operation_id": "outcome-rev-1", "actor": ACTOR, "capability": "cap.outreach.outcome",
                          "args": {"lead_id": d["lead_id"], "outcome": "paid_unverified", "evidence": "said paid",
                                   "revenue": 120}})
    if out.status is not Status.REJECTED:
        v.append("revenue field accepted")
    o.h.cp.execute({"operation_id": "outcome-rev-2", "actor": ACTOR, "capability": "cap.outreach.outcome",
                    "args": {"lead_id": d["lead_id"], "outcome": "paid_unverified", "evidence": "said paid"}})
    if o.store.outcome(d["lead_id"])["revenue"]["value"] is not None:
        v.append("revenue not UNKNOWN")
    return v


def default_deny_and_dry_run(o):
    o.build()
    d = o.pending()[0]
    out = o.send(d, o.token(d))
    v = [] if out.status is Status.EXECUTED and out.result.get("dry_run") and out.result.get("forced") else [str(out)]
    if o.sent_count() or o.store.draft(d["draft_id"])["state"] != "pending_approval":
        v.append("default context sent or changed state")
    o.ctx.dry_run = False                                   # operator flips dry-run off; transport still denies
    out = o.send(d, o.token(d, attempt=2), attempt=2)
    if out.reason != "worker:outreach:transport_disabled" or o.sent_count():
        v.append(f"deny transport: {out.reason}")
    return v


def scheduler_cannot_send(o):
    store = JobStore(os.path.join(o.h.dir.name, "jobs.sqlite"), b"s" * 32)
    try:
        s = Scheduler(o.h.cp, store, o.h.audit, o.h.kill, clock=o.h.wall)
        o.build()
        d = o.pending()[0]
        try:
            s.define("outreach.auto", ACTOR, [{"capability": "cap.outreach.send", "args": {
                "draft_id": d["draft_id"], "draft_hash": d["draft_hash"], "recipient": d["recipient"],
                "channel": "email", "mode": "live"}}], {"kind": "interval", "every_s": 60, "anchor": 0})
        except DefinitionError as exc:
            return [] if exc.code == "risk_exceeds_unattended_ceiling" else [f"refused for {exc.code}"]
        return ["scheduler accepted an unattended send"]
    finally:
        store.close()


CASES = [no_token, token_for_other_draft, token_other_recipient_or_channel, tamper_after_approval,
         suppressed_recipient, opt_out_after_approval, day_cap_split, recipient_cap_split, double_send,
         injection_in_lead_fields, revenue_forged, scheduler_cannot_send, dry_run_mode_request, unsafe_charset_names]


class OutreachAdversarial(unittest.TestCase):
    def test_zero_violations(self):
        bad = {}
        for case in CASES + [default_deny_and_dry_run]:
            o = OutreachHarness(ctx_defaults=case is default_deny_and_dry_run)
            try:
                v = case(o)
                if v:
                    bad[case.__name__] = v
            finally:
                o.close()
        n = len(CASES) + 1
        print(f"\nOUTREACH_ADVERSARIAL cases={n} violations={len(bad)}", file=sys.stderr)
        self.assertGreaterEqual(n, 10)
        self.assertEqual(bad, {})


if __name__ == "__main__":
    unittest.main()
