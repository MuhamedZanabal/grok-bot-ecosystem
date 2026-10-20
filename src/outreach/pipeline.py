"""Build (ingest -> score -> draft -> enqueue via ControlPlane) and review export (cycle-010).

python -m outreach build  --state DIR --pipeline CSV [--omega CSV ...] [--min-score N]
python -m outreach export --state DIR --jsonl OUT.jsonl --md OUT.md
cycle-011 (D-0028) handoff commands, see handoff.py:
  grant-standing | revoke-grant | send-plan | verify-manifest | record-sent | record-failed | record-reply | status
The state dir holds the audit log, CP ledgers, the outbox and its MAC key (0600). The approval key
lives OUTSIDE the state dir (--approval-key, $OUTREACH_APPROVAL_KEY, or ~/.config/grok-bot/; S-0013).
build/export run with DenyTransport + dry_run=True; send-plan uses HandoffTransport, which reserves
and never delivers. Nothing here can deliver a message.
"""

import json
import os
import time

from control_plane import (ApprovalAuthority, AuditLog, Budget, ControlPlane, GrantStore, IdempotencyLedger,
                           KillSwitch, LoopGovernor, PolicyEngine, Status, redact)

from .drafts import make_draft
from . import handoff
from .drafts import TEMPLATE_ID
from .handlers import (CONFIRM_CAP, ENQUEUE_CAP, OUTCOME_CAP, REPLY_CAP, SEND_CAP, HandoffTransport, OutreachContext,
                       register_outreach)
from .issuer import GrantError, StandingIssuer
from .leads import ingest, score
from .store import OutboxStore

ACTOR = "operator.outreach"
DEFAULT_APPROVAL_KEY = "~/.config/grok-bot/outreach_approval.key"


class KeyCustodyError(Exception):
    pass


def approval_key(state: str, path: str | None = None) -> bytes:
    """Approval key outside the state dir, 0600 in a 0700 dir; refused if inside the state dir or
    readable by group/other. Same host and uid (S-0013): custody is separated, not isolated."""
    p = os.path.realpath(os.path.expanduser(path or os.environ.get("OUTREACH_APPROVAL_KEY") or DEFAULT_APPROVAL_KEY))
    st = os.path.realpath(state)
    if p == st or p.startswith(st + os.sep):
        raise KeyCustodyError("approval_key_inside_state_dir")
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    key = _key(p)
    if os.stat(p).st_mode & 0o077:
        raise KeyCustodyError("approval_key_permissions")
    return key


def _key(path: str) -> bytes:
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(os.urandom(32))
    with open(path, "rb") as fh:
        return fh.read()


def open_runtime(state: str, *, clock=time.time, approval_key_path: str | None = None, handoff_mode: bool = False):
    os.makedirs(state, mode=0o700, exist_ok=True)
    akey = approval_key(state, approval_key_path)
    p = lambda n: os.path.join(state, n)
    audit = AuditLog(p("audit.jsonl"))
    policy = PolicyEngine(GrantStore(p("grants.sqlite")), audit)
    kill = KillSwitch(p("KILL"))
    cp = ControlPlane(policy=policy, approvals=ApprovalAuthority(akey, clock=clock),
                      ledger=IdempotencyLedger(p("ops.sqlite")), audit=audit, kill_switch=kill,
                      governor=LoopGovernor(Budget(100_000, 86_400, 1e9), kill), require_host_hardening=False)
    ctx = OutreachContext(store=OutboxStore(p("outbox.sqlite"), _key(p("outbox.key"))), audit=audit, clock=clock)
    if handoff_mode:              # reserve-only transport; still no delivery path in this process
        ctx.transport, ctx.dry_run = HandoffTransport(), False
    register_outreach(cp, ctx)
    for cap in (ENQUEUE_CAP, OUTCOME_CAP, SEND_CAP, CONFIRM_CAP, REPLY_CAP):   # SEND needs a per-draft token
        if cap not in {c.id for c in policy.granted(ACTOR)}:
            policy.grant(ACTOR, cap, reason="outreach operator (cycle-010/011)")
    return cp, ctx, StandingIssuer(akey, ctx.store, audit, clock)


def build(cp, ctx, sources, *, min_score: int = 0, actor: str = ACTOR) -> dict:
    leads, st = ingest(sources)
    now = ctx.clock()
    stats = {**st, "held": 0, "suppressed": 0, "prior_pending": 0, "no_email": 0, "below_min_score": 0,
             "drafts_created": 0, "drafts_existing": 0, "enqueue_refused": 0}
    for lead in leads:
        f = lead["fields"]
        if lead["suppress"]:
            stats["suppressed"] += 1
            if f.get("email"):
                ctx.store.suppress(f["email"], lead["suppress"], "pipeline.csv:status", now)
            continue
        sc = score(lead)
        d = make_draft(lead, sc)
        if d is None:
            key = "held" if lead["hold"] else "prior_pending" if lead["prior_pending"] else "no_email"
            stats[key] += 1
            continue
        if sc["score"] < min_score:
            stats["below_min_score"] += 1
            continue
        out = cp.execute({"operation_id": f"enqueue:{lead['lead_id'][5:]}:{d['template_id']}", "actor": actor,
                          "capability": ENQUEUE_CAP, "args": d})
        if out.status in (Status.EXECUTED, Status.REPLAYED):
            stats["drafts_created" if out.result.get("created") and out.status is Status.EXECUTED
                  else "drafts_existing"] += 1
        else:
            stats["enqueue_refused"] += 1
    stats["pending_approval"] = len(ctx.store.drafts("pending_approval"))
    return stats


def export_pending(store, jsonl_path: str, md_path: str) -> int:
    rows = [d for d in store.drafts("pending_approval") if d["intact"]]
    recs = [redact({"draft_id": d["draft_id"], "draft_hash": d["draft_hash"], "lead_id": d["lead_id"],
                    "channel": d["channel"], "recipient": d["recipient"], "subject": d["subject"], "body": d["body"],
                    "state": d["state"], "score": d["meta"]["score"], "evidence": d["meta"]["evidence"],
                    "personalization": d["personalization"]}) for d in rows]
    for path, text in ((jsonl_path, "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in recs)),
                       (md_path, _markdown(recs))):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    return len(recs)


def _markdown(recs) -> str:
    L = ["# Outreach drafts pending approval", "", f"{len(recs)} draft(s). Nothing has been sent. Each send needs "
         "an approval token bound to the draft hash, recipient, channel and mode.", ""]
    for r in recs:
        L += [f"## {r['draft_id']} (score {r['score']})", f"- to: `{r['recipient']}` via {r['channel']}",
              f"- draft_hash: `{r['draft_hash']}`",
              "- evidence: " + "; ".join(f"{e['rule']}(+{e['points']}) from {e['field']}" for e in r["evidence"]),
              "- personalization: " + "; ".join(f"{p['slot']}<-{p['field']}@{p['source']['source']}:{p['source']['row']}"
                                                for p in r["personalization"]),
              f"- subject: {r['subject']}", "", "```text", r["body"].rstrip("\n"), "```", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="outreach")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, *args):
        sp = sub.add_parser(name)
        sp.add_argument("--state", required=True)
        sp.add_argument("--approval-key", default=None)
        for flag, kw in args:
            sp.add_argument(flag, **kw)
        return sp
    req, rep = {"required": True}, {"action": "append", "default": []}
    cmd("build", ("--pipeline", rep), ("--omega", rep), ("--min-score", {"type": int, "default": 0}))
    cmd("export", ("--jsonl", req), ("--md", req))
    cmd("grant-standing", ("--ref", req), ("--granted-on", req), ("--statement", req),
        ("--max-sends", {"type": int, "required": True}), ("--granted-by", req), ("--template", {"default": TEMPLATE_ID}))
    cmd("revoke-grant", ("--ref", req))
    cmd("send-plan", ("--grant", req), ("--out", req))
    cmd("verify-manifest", ("--manifest", req))
    cmd("record-sent", ("--reservation", req), ("--message-id", req))
    cmd("record-failed", ("--reservation", req), ("--reason", req))
    cmd("record-reply", ("--from", {"dest": "sender", "required": True}), ("--text-file", req), ("--message-id", req))
    cmd("status")
    a = ap.parse_args(argv)
    try:
        cp, ctx, issuer = open_runtime(a.state, approval_key_path=a.approval_key, handoff_mode=a.cmd == "send-plan")
    except KeyCustodyError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    rc = 0
    if a.cmd == "build":
        src = [(os.path.basename(x), "pipeline", x) for x in a.pipeline] + \
              [(os.path.basename(x), "omega", x) for x in a.omega]
        res = build(cp, ctx, src, min_score=a.min_score)
    elif a.cmd == "export":
        res = {"exported_pending": export_pending(ctx.store, a.jsonl, a.md)}
    elif a.cmd == "grant-standing":
        try:
            res = issuer.record(a.ref, granted_on=a.granted_on, statement=a.statement, max_sends=a.max_sends,
                                template_id=a.template, granted_by=a.granted_by)
        except GrantError as exc:
            res, rc = {"error": exc.code}, 2
    elif a.cmd == "revoke-grant":
        res = {"revoked": issuer.revoke(a.ref)}
    elif a.cmd == "send-plan":
        res = handoff.send_plan(cp, ctx, issuer, a.grant, a.out, ACTOR)
        res["legacy_approval_key_in_state"] = os.path.exists(os.path.join(a.state, "approval.key"))
    elif a.cmd == "verify-manifest":
        res = handoff.verify_manifest(ctx, a.manifest)
        rc = 0 if res["ok"] == res["total"] else 1
    elif a.cmd == "record-reply":
        with open(a.text_file, encoding="utf-8", errors="replace") as fh:
            out = handoff.record_reply(cp, ACTOR, a.sender, fh.read(), a.message_id)
        res = {"status": out.status.value, "reason": out.reason, "result": out.result}
        rc = 0 if out.status in (Status.EXECUTED, Status.REPLAYED) else 2
    elif a.cmd == "status":
        res = handoff.status(ctx)
    else:
        sent = a.cmd == "record-sent"
        out = handoff.record(cp, ctx, ACTOR, a.reservation, "sent" if sent else "failed",
                             a.message_id if sent else None, None if sent else a.reason)
        res = {"status": out.status.value, "reason": out.reason, "result": out.result}
        rc = 2 if out.status not in (Status.EXECUTED, Status.REPLAYED) else 3 if (out.result or {}).get("anomalies") else 0
    print(json.dumps(res, sort_keys=True, ensure_ascii=False))
    return rc
