"""Two-phase Gmail handoff (cycle-011, D-0028).

Phase 1  send_plan: for each pending draft, the standing issuer mints a per-draft token and
         cap.outreach.send(mode=handoff) reserves it under the caps. Output: manifest.json plus one
         read-only body file per reservation. Only entries in the manifest may be sent.
Check    verify_manifest: re-checks every entry against the store right before sending
         (reservation still `reserved`, not expired, recipient not suppressed, draft MAC/hash,
         manifest fields and body file unchanged). Send only entries whose status is "ok".
Phase 2  record: cap.outreach.confirm records sent (with the Gmail message id) or failed.
Reply    record_reply: cap.outreach.reply; opt-out keywords suppress the sender.
No function here delivers mail.
"""

import hashlib
import json
import os
import time
from collections import Counter

from control_plane import Status

from .handlers import CONFIRM_CAP, REPLY_CAP, send_request
from .issuer import GrantError

MANIFEST = "manifest.json"


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _sha_file(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return "missing"


def _write(path: str, text: str, mode: int) -> None:
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def send_plan(cp, ctx, issuer, grant_ref: str, out_dir: str, actor: str) -> dict:
    now = ctx.clock()
    in_doubt = ctx.store.sweep(now)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    skipped, attempt, new = Counter(), int(now // 86400), 0     # one attempt per UTC cap day per draft
    for d in ctx.store.drafts("pending_approval"):
        if not d["intact"]:
            skipped["draft_tampered"] += 1
            continue
        req = send_request(actor, d, "handoff", attempt, grant_ref=grant_ref)
        try:
            req["approval_token"] = issuer.token(req, d)
        except GrantError as exc:
            skipped[exc.code] += 1
            continue
        out = cp.execute(req)
        if out.status is not Status.EXECUTED or not out.result.get("reserved"):
            skipped[(out.reason or out.status.value).replace("worker:outreach:", "")] += 1
            continue
        rid, new = out.result["reservation_id"], new + 1
        body_file = os.path.join(out_dir, rid[4:] + ".txt")
        _write(body_file, d["body"], 0o400)
        with ctx.store.lock:
            ctx.store.db.execute("UPDATE reservations SET body_file=? WHERE reservation_id=?", (body_file, rid))
    entries = []      # every open reservation, so re-running send-plan never loses an unsent entry
    for r in ctx.store.reservations("reserved"):
        d = ctx.store.draft(r["draft_id"])
        if d is None or not d["intact"] or d["draft_hash"] != r["draft_hash"]:
            skipped["open_reservation_draft_changed"] += 1
            continue
        entries.append({"reservation_id": r["reservation_id"], "recipient": d["recipient"], "subject": d["subject"],
                        "body_file": r["body_file"], "channel": d["channel"], "draft_id": d["draft_id"],
                        "draft_hash": d["draft_hash"], "body_sha256": r["body_sha256"],
                        "expires_at": _iso(r["expires_at"])})
    path = os.path.join(out_dir, MANIFEST)
    doc = {"generated_at": _iso(now), "cap_day_utc": time.strftime("%Y-%m-%d", time.gmtime(now)),
           "grant_ref": grant_ref, "entries": entries,
           "instructions": "Run verify-manifest first; send only entries with status ok, exactly the body file "
                           "text and subject to the recipient; then record-sent or record-failed per reservation."}
    _write(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n", 0o600)
    ctx.audit.append("outreach_send_plan", reserved=new, manifest_entries=len(entries), skipped=dict(skipped), in_doubt_swept=in_doubt,
                     grant_ref=grant_ref)
    return {"manifest": path, "reserved": new, "manifest_entries": len(entries), "skipped": dict(skipped), "in_doubt_swept": in_doubt,
            "pending_left": len(ctx.store.drafts("pending_approval"))}


def check_entry(ctx, e: dict, now: float) -> list[str]:
    r = ctx.store.reservation(e.get("reservation_id", ""))
    if r is None:
        return ["not_reserved"]
    d = ctx.store.draft(r["draft_id"])
    probs = [] if r["state"] == "reserved" else [f"state_{r['state']}"]
    if now >= r["expires_at"]:
        probs.append("expired")
    if d is None or not d["intact"] or d["draft_hash"] != r["draft_hash"]:
        return probs + ["draft_changed"]
    if ctx.store.suppressed(d["recipient"]):
        probs.append("suppressed")
    if (e.get("recipient"), e.get("subject"), e.get("body_file"), e.get("draft_hash")) != \
            (d["recipient"], d["subject"], r["body_file"], r["draft_hash"]):
        probs.append("manifest_edited")
    if _sha_file(r["body_file"]) != r["body_sha256"]:
        probs.append("body_file_changed")
    return probs


def verify_manifest(ctx, path: str) -> dict:
    now = ctx.clock()
    ctx.store.sweep(now)
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    res = [{"reservation_id": e.get("reservation_id"), "status": ",".join(check_entry(ctx, e, now)) or "ok"}
           for e in doc.get("entries", [])]
    return {"ok": sum(r["status"] == "ok" for r in res), "total": len(res), "entries": res}


def record(cp, ctx, actor: str, rid: str, result: str, message_id: str | None = None, detail: str | None = None):
    r = ctx.store.reservation(rid)
    args = {"reservation_id": rid, "result": result,
            "body_sha256": _sha_file(r["body_file"]) if r and r["body_file"] else "missing"}
    if message_id is not None:
        args["message_id"] = message_id
    if detail:
        args["detail"] = detail
    return cp.execute({"operation_id": f"confirm:{rid}", "actor": actor, "capability": CONFIRM_CAP, "args": args})


def record_reply(cp, actor: str, sender: str, text: str, message_id: str):
    return cp.execute({"operation_id": "reply:" + hashlib.sha256(message_id.encode()).hexdigest()[:32],
                       "actor": actor, "capability": REPLY_CAP,
                       "args": {"sender": sender, "text": text[:20000], "message_id": message_id}})


def status(ctx) -> dict:
    ctx.store.sweep(ctx.clock())
    q = lambda t: dict(ctx.store.db.execute(f"SELECT state, COUNT(*) FROM {t} GROUP BY state").fetchall())
    return {"drafts": q("drafts"), "reservations": q("reservations"),
            "sends": ctx.store.db.execute("SELECT COUNT(*) FROM sends").fetchone()[0],
            "suppressed": ctx.store.db.execute("SELECT COUNT(*) FROM suppression").fetchone()[0]}
