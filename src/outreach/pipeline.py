"""Build (ingest -> score -> draft -> enqueue via ControlPlane) and review export (cycle-010).

python -m outreach build  --state DIR --pipeline CSV [--omega CSV ...] [--min-score N]
python -m outreach export --state DIR --jsonl OUT.jsonl --md OUT.md
The state dir holds the audit log, CP ledgers, the outbox and MAC/approval keys (0600). The
runtime is wired with DenyTransport + dry_run=True; nothing here can deliver a message.
"""

import json
import os
import time

from control_plane import (ApprovalAuthority, AuditLog, Budget, ControlPlane, GrantStore, IdempotencyLedger,
                           KillSwitch, LoopGovernor, PolicyEngine, Status, redact)

from .drafts import make_draft
from .handlers import ENQUEUE_CAP, OUTCOME_CAP, SEND_CAP, OutreachContext, register_outreach
from .leads import ingest, score
from .store import OutboxStore

ACTOR = "operator.outreach"


def _key(path: str) -> bytes:
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(os.urandom(32))
    with open(path, "rb") as fh:
        return fh.read()


def open_runtime(state: str, *, clock=time.time):
    os.makedirs(state, mode=0o700, exist_ok=True)
    p = lambda n: os.path.join(state, n)
    audit = AuditLog(p("audit.jsonl"))
    policy = PolicyEngine(GrantStore(p("grants.sqlite")), audit)
    kill = KillSwitch(p("KILL"))
    cp = ControlPlane(policy=policy, approvals=ApprovalAuthority(_key(p("approval.key")), clock=clock),
                      ledger=IdempotencyLedger(p("ops.sqlite")), audit=audit, kill_switch=kill,
                      governor=LoopGovernor(Budget(100_000, 86_400, 1e9), kill), require_host_hardening=False)
    ctx = OutreachContext(store=OutboxStore(p("outbox.sqlite"), _key(p("outbox.key"))), audit=audit, clock=clock)
    register_outreach(cp, ctx)
    for cap in (ENQUEUE_CAP, OUTCOME_CAP, SEND_CAP):     # SEND still needs a per-draft approval token
        if cap not in {c.id for c in policy.granted(ACTOR)}:
            policy.grant(ACTOR, cap, reason="cycle-010 outreach operator")
    return cp, ctx


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
    b = sub.add_parser("build")
    b.add_argument("--state", required=True)
    b.add_argument("--pipeline", action="append", default=[])
    b.add_argument("--omega", action="append", default=[])
    b.add_argument("--min-score", type=int, default=0)
    e = sub.add_parser("export")
    e.add_argument("--state", required=True)
    e.add_argument("--jsonl", required=True)
    e.add_argument("--md", required=True)
    a = ap.parse_args(argv)
    cp, ctx = open_runtime(a.state)
    if a.cmd == "build":
        src = [(os.path.basename(x), "pipeline", x) for x in a.pipeline] + \
              [(os.path.basename(x), "omega", x) for x in a.omega]
        print(json.dumps(build(cp, ctx, src, min_score=a.min_score), sort_keys=True))
    else:
        print(json.dumps({"exported_pending": export_pending(ctx.store, a.jsonl, a.md)}))
    return 0
