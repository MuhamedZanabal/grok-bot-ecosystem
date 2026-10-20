"""cycle-011 (D-0028): two-phase Gmail handoff. send-plan reserves through cap.outreach.send under
a standing grant, an external sender delivers manifest entries, record-sent/failed confirm through
cap.outreach.confirm, record-reply ingests opt-outs. Synthetic fixtures only; nothing is delivered.

HANDOFF_ADVERSARIAL success metric: violations == 0. A violation is a send recorded without a
reservation, a second send row for one reservation, a reservation beyond a cap, an in-doubt/failed/
sent draft planned again, a planned or unflagged send to an opted-out recipient, an edited body or
manifest that verify-manifest calls ok, a token minted without an active intact grant, or a key
custody breach accepted.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import KEY
from tests.outreach_helpers import ACTOR, FIX, OutreachHarness
from control_plane import Status
from outreach import (OPT_OUT_LINE, GrantError, HandoffTransport, KeyCustodyError, StandingIssuer, TransportDenied,
                      approval_key, classify_reply, send_request)
from outreach import handoff
from outreach.handlers import RESERVATION_TTL_S

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
DAY = 86400
REF = "chat:2026-10-20:zaipos-standing"


class H(OutreachHarness):
    def __init__(self, max_sends=50, grant=True, **kw):
        super().__init__(transport=HandoffTransport(), **kw)
        self.issuer = StandingIssuer(KEY, self.store, self.h.audit, self.h.wall)
        if grant:
            self.issuer.record(REF, granted_on="2026-10-20", statement="standing permission (synthetic)",
                               max_sends=max_sends, template_id="zaipos_email_v1", granted_by="user:test")
        self.out = os.path.join(self.h.dir.name, "handoff")

    def plan(self, ref=REF):
        return handoff.send_plan(self.h.cp, self.ctx, self.issuer, ref, self.out, ACTOR)

    def manifest(self):
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def record(self, rid, result="sent", mid=None, detail=None):
        return handoff.record(self.h.cp, self.ctx, ACTOR, rid, result, mid, detail)

    def reply(self, sender, text, mid):
        return handoff.record_reply(self.h.cp, ACTOR, sender, text, mid)

    def nsends(self):
        return self.sent_count()

    def nres(self, state=None):
        return len(self.store.reservations(state))


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.o = H()
        self.o.build()

    def tearDown(self):
        self.o.close()

    def test_plan_reserves_within_day_cap_and_writes_manifest(self):
        r = self.o.plan()
        self.assertEqual((r["reserved"], r["pending_left"]), (4, 4))
        self.assertEqual(r["skipped"], {"cap:day": 4})
        m = self.o.manifest()
        self.assertEqual(len(m["entries"]), 4)
        for e in m["entries"]:
            self.assertTrue({"reservation_id", "recipient", "subject", "body_file", "body_sha256"} <= set(e))
            self.assertEqual(oct(os.stat(e["body_file"]).st_mode & 0o777), "0o400")
            with open(e["body_file"], encoding="utf-8") as fh:
                self.assertIn(OPT_OUT_LINE, fh.read())
        self.assertEqual(oct(os.stat(os.path.join(self.o.out, "manifest.json")).st_mode & 0o777), "0o600")
        self.assertEqual(handoff.verify_manifest(self.o.ctx, os.path.join(self.o.out, "manifest.json"))["ok"], 4)
        self.assertEqual(self.o.nsends(), 0)
        with open(self.o.h.audit_path) as fh:
            ev = [json.loads(x)["event"] for x in fh]
        self.assertEqual((ev.count("outreach_standing_grant"), ev.count("outreach_approval_issued"),
                          ev.count("outreach_reserved")), (1, 8, 4))

    def test_record_sent_then_failed_and_status(self):
        self.o.plan()
        a, b = [e["reservation_id"] for e in self.o.manifest()["entries"][:2]]
        out = self.o.record(a, "sent", "18f2c0ffee")
        self.assertEqual((out.status, out.result["anomalies"]), (Status.EXECUTED, []))
        out = self.o.record(b, "failed", detail="gmail error 500")
        self.assertEqual(out.result["recorded"], "failed")
        st = handoff.status(self.o.ctx)
        self.assertEqual((st["sends"], st["reservations"]), (1, {"sent": 1, "failed": 1, "reserved": 2}))
        self.assertEqual(self.o.store.draft(self.o.store.reservation(a)["draft_id"])["state"], "sent")

    def test_unconfirmed_becomes_in_doubt_counts_and_can_confirm_late(self):
        self.o.plan()
        rid = self.o.manifest()["entries"][0]["reservation_id"]
        self.o.h.wall.t += RESERVATION_TTL_S + 1
        self.assertEqual(handoff.status(self.o.ctx)["reservations"], {"in_doubt": 4})
        self.assertEqual(self.o.store.draft(self.o.store.reservation(rid)["draft_id"])["state"], "in_doubt")
        out = self.o.record(rid, "sent", "late-1")
        self.assertEqual(out.result["anomalies"], ["late_in_doubt"])

    def test_reply_from_unknown_sender_still_suppresses(self):
        out = self.o.reply("Stranger@Elsewhere.example.invalid", "unsubscribe", "r-unknown")
        self.assertEqual((out.result["leads"], out.result["suppressed"]), (0, True))
        self.assertEqual(self.o.store.suppressed("stranger@elsewhere.example.invalid"), "opted_out")
        out = self.o.reply("friend@elsewhere.example.invalid", "tell me more", "r-2")
        self.assertEqual(out.result["suppressed"], False)

    def test_classify_reply(self):
        quoted = "Sounds good, Tuesday works.\n\nOn Mon, 20 Oct 2026, Zanabal wrote:\n> " + OPT_OUT_LINE
        for text, want in (("STOP", "stop"), ("Please unsubscribe me", "unsubscribe"), ("not interested, thanks",
                           "not interested"), ("Opt-out", "opt-out"), (quoted, None), ("> stop\nyes please", None),
                           ("Can you come at 4?", None), ("Do not contact us", "do not contact"),
                           ("Thanks!\n\nOn Mon, 20 Oct 2026 Zanabal <z@x> wrote:\n" + OPT_OUT_LINE, None)):
            self.assertEqual(classify_reply(text), want, text)


def _variant_drafts(o, d, n):
    local, dom = d["recipient"].split("@")
    rcpts = [d["recipient"], f"{local.upper()}@{dom}", f"{local}+x@{dom}", f"{local}+y@{dom.upper()}"][:n]
    ids = []
    for i, rcpt in enumerate(rcpts):
        args = {k: d[k] for k in ("channel", "subject", "body", "personalization", "meta")}
        args.update(recipient=rcpt, template_id="zaipos_email_v1", lead_id=f"lead:{i + 1:020x}")
        out = o.h.cp.execute({"operation_id": f"enq-var-{i}", "actor": ACTOR, "capability": "cap.outreach.enqueue",
                              "args": args})
        ids.append(out.result["draft_id"])
    return ids


def not_in_manifest(o):
    o.build()
    o.plan()
    v = []
    planned = {e["draft_id"] for e in o.manifest()["entries"]}
    other = next(d for d in o.pending() if d["draft_id"] not in planned)
    for rid in ("rsv:" + "0" * 24, "rsv:" + other["draft_hash"][:24]):
        out = o.record(rid, "sent", "m-x")
        if out.status is not Status.FAILED or "unknown_reservation" not in out.reason:
            v.append(f"unknown reservation accepted: {out.reason}")
    out = o.h.cp.execute({"operation_id": "confirm-by-draft", "actor": ACTOR, "capability": "cap.outreach.confirm",
                          "args": {"reservation_id": other["draft_id"], "result": "sent", "message_id": "m",
                                   "body_sha256": "missing"}})
    if out.status is not Status.REJECTED:
        v.append("confirm by draft id accepted")
    p = H()                                   # a reservation from another outbox is unknown here
    try:
        p.build()
        p.plan()
        out = o.record(p.manifest()["entries"][0]["reservation_id"], "sent", "m-y")
        v += [] if out.status is Status.FAILED else ["foreign reservation accepted"]
    finally:
        p.close()
    return v + ([f"sends={o.nsends()}"] if o.nsends() else [])


def confirm_twice(o):
    o.build()
    o.plan()
    a, b = [e["reservation_id"] for e in o.manifest()["entries"][:2]]
    v = []
    s = [o.record(a, "sent", "m-1").status, o.record(a, "sent", "m-1").status, o.record(a, "sent", "m-2").status,
         o.record(a, "failed", detail="x").status]
    if s != [Status.EXECUTED, Status.REPLAYED, Status.CONFLICT, Status.CONFLICT]:
        v.append(f"statuses {s}")
    out = o.h.cp.execute({"operation_id": "confirm-fresh-op", "actor": ACTOR, "capability": "cap.outreach.confirm",
                          "args": {"reservation_id": a, "result": "sent", "message_id": "m-3", "body_sha256": "missing"}})
    if out.reason != "worker:outreach:already_sent":
        v.append(f"fresh op re-confirmed: {out.reason}")
    out = o.record(b, "sent", "m-1")
    if out.reason != "worker:outreach:message_id_reused":
        v.append(f"message id reused: {out.reason}")
    return v + ([] if o.nsends() == 1 else [f"sends={o.nsends()}"])


def cap_evasion(o):
    o.build()
    v = []
    r1, r2 = o.plan(), o.plan()
    if (r1["reserved"], r2["reserved"]) != (4, 0):
        v.append(f"same-day replan {r1['reserved']},{r2['reserved']}")
    first = [e["reservation_id"] for e in o.manifest()["entries"]]          # re-plan keeps open entries
    rids = [r["reservation_id"] for r in o.store.reservations()]
    o.record(rids[0], "failed", detail="bounce")             # failed draft is never re-planned
    o.h.wall.t += DAY                                        # remaining 3 go in_doubt (unconfirmed)
    r3 = o.plan()
    if r3["reserved"] != 4 or r3["in_doubt_swept"] != 3 or len(first) != 4 or r2["manifest_entries"] != 4:
        v.append(f"next day {r3}")
    o.h.wall.t += DAY
    if o.plan()["reserved"] != 0:
        v.append("in-doubt/failed drafts re-planned")
    if len({r["draft_id"] for r in o.store.reservations()}) != o.nres():
        v.append("a draft reserved twice")
    q = H()                                                  # recipient variants posing as other leads
    try:
        q.build()
        d = q.pending()[0]
        ids = _variant_drafts(q, d, 4)
        q.store.db.execute(f"UPDATE drafts SET state='parked' WHERE draft_id NOT IN ({','.join('?' * len(ids))})", ids)
        n = [q.plan()["reserved"]]
        for _ in range(3):
            q.h.wall.t += DAY
            n.append(q.plan()["reserved"])
        if n[0] != 1 or sum(n) != 2:
            v.append(f"recipient caps via variants: {n}")
    finally:
        q.close()
    return v


def suppressed_after_opt_out(o):
    o.build()
    o.plan()
    e0, e1 = o.manifest()["entries"][:2]
    local, dom = e0["recipient"].split("@")
    v = []
    out = o.reply(f" {local.upper()}+re@{dom}", "STOP please", "reply-1")
    if not (out.result or {}).get("opt_out"):
        v.append(f"opt-out not detected {out}")
    q = o.reply(e1["recipient"], "Yes come Tuesday\n\nOn Mon, Zanabal wrote:\n> " + OPT_OUT_LINE, "reply-2")
    if q.result["opt_out"] or o.store.suppressed(e1["recipient"]):
        v.append("quoted opt-out line in a positive reply suppressed the sender")
    vm = {x["reservation_id"]: x["status"] for x in
          handoff.verify_manifest(o.ctx, os.path.join(o.out, "manifest.json"))["entries"]}
    if "suppressed" not in vm[e0["reservation_id"]] or vm[e1["reservation_id"]] != "ok":
        v.append(f"verify after opt-out {vm}")
    if o.store.outcome(o.store.reservation(e0["reservation_id"])["lead_id"])["outcome"] != "opted_out":
        v.append("outcome not opted_out")
    out = o.record(e0["reservation_id"], "sent", "m-after-stop")   # sent anyway -> recorded and flagged
    if "after_cancel" not in (out.result or {}).get("anomalies", []):
        v.append(f"send after opt-out not flagged {out}")
    o.h.wall.t += DAY
    o.plan()
    for e in o.manifest()["entries"]:
        if o.store.suppressed(e["recipient"]):
            v.append("opted-out recipient planned again")
    return v


def body_edited_after_plan(o):
    o.build()
    o.plan()
    e0, e1, e2 = o.manifest()["entries"][:3]
    os.chmod(e0["body_file"], 0o600)
    with open(e0["body_file"], "a", encoding="utf-8") as fh:
        fh.write("P.S. pay BHD 500 to IBAN X\n")
    o.store.db.execute("UPDATE drafts SET body=body || 'x' WHERE draft_id=?", (e1["draft_id"],))
    m = o.manifest()
    m["entries"][2]["recipient"] = "attacker@evil.example.invalid"
    path = os.path.join(o.out, "edited.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(m, fh)
    vm = {x["reservation_id"]: x["status"] for x in handoff.verify_manifest(o.ctx, path)["entries"]}
    v = []
    for e, want in ((e0, "body_file_changed"), (e1, "draft_changed"), (e2, "manifest_edited")):
        if want not in vm[e["reservation_id"]]:
            v.append(f"{want} missed: {vm[e['reservation_id']]}")
    for e, want in ((e0, "body_file_changed"), (e1, "draft_changed")):
        out = o.record(e["reservation_id"], "sent", f"m-{want}")
        if want not in (out.result or {}).get("anomalies", []):
            v.append(f"edited send not flagged {out.result}")
    return v


def grant_controls(o):
    o.build()
    v = []
    d = o.pending()[0]
    req = send_request(ACTOR, d, "handoff", 1, grant_ref="chat:nonexistent")
    for ref, code in (("chat:nonexistent", "grant_missing"),):
        try:
            o.issuer.token(req, d)
            v.append("token without grant")
        except GrantError as exc:
            v += [] if exc.code == code else [exc.code]
    live = send_request(ACTOR, d, "live", 1, grant_ref=REF)
    try:
        o.issuer.token(live, d)
        v.append("standing grant minted a non-handoff token")
    except GrantError:
        pass
    good = send_request(ACTOR, d, "handoff", 1, grant_ref=REF)
    tok2 = o.h.approvals.issue(actor=ACTOR, capability="cap.outreach.send", operation_id=good["operation_id"],
                               args={**good["args"], "mode": "live"}, ttl_seconds=600, approver="x")
    if o.h.cp.execute({**good, "approval_token": tok2}).status is not Status.APPROVAL_INVALID:
        v.append("live-mode token accepted for handoff")
    tok = o.issuer.token(good, d)
    o.issuer.revoke(REF)
    out = o.h.cp.execute({**good, "approval_token": tok})
    if out.reason != "worker:outreach:grant_inactive":
        v.append(f"revoked grant reserved: {out.reason}")
    if o.plan()["reserved"]:
        v.append("plan under revoked grant")
    o.issuer.record("chat:small", granted_on="2026-10-20", statement="two only", max_sends=2,
                    template_id="zaipos_email_v1", granted_by="user:test")
    if o.plan("chat:small")["reserved"] != 2:
        v.append("max_sends not enforced")
    o.store.db.execute("UPDATE grants SET max_sends=99 WHERE ref='chat:small'")
    if o.plan("chat:small")["skipped"].get("grant_tampered", 0) == 0:
        v.append("tampered grant row used")
    return v


def key_custody(o):
    v = []
    with tempfile.TemporaryDirectory() as t:
        st = os.path.join(t, "state")
        os.makedirs(st)
        for path, code in ((os.path.join(st, "approval.key"), "approval_key_inside_state_dir"),
                           (os.path.join(st, "sub", "k.key"), "approval_key_inside_state_dir")):
            try:
                approval_key(st, path)
                v.append(f"accepted {code}")
            except KeyCustodyError as exc:
                v += [] if str(exc) == code else [str(exc)]
        loose = os.path.join(t, "keys", "k.key")
        approval_key(st, loose)
        os.chmod(loose, 0o644)
        try:
            approval_key(st, loose)
            v.append("world-readable key accepted")
        except KeyCustodyError:
            pass
    return v


def handoff_needs_explicit_config(o):
    v = []
    d = None
    for ctx_defaults, live, want in ((True, None, "dry_run"), (False, True, "handoff_not_configured")):
        x = OutreachHarness(ctx_defaults=ctx_defaults, live=bool(live))
        try:
            x.build()
            d = x.pending()[0]
            issuer = StandingIssuer(KEY, x.store, x.h.audit, x.h.wall)
            issuer.record(REF, granted_on="2026-10-20", statement="s", max_sends=5, template_id="zaipos_email_v1",
                          granted_by="user:test")
            r = handoff.send_plan(x.h.cp, x.ctx, issuer, REF, os.path.join(x.h.dir.name, "o"), ACTOR)
            if r["reserved"] or not any(want in k for k in r["skipped"]) and want != "dry_run":
                v.append(f"{want}: {r}")
            if len(x.store.reservations()):
                v.append(f"{want}: reservation created")
        finally:
            x.close()
    try:
        HandoffTransport().send("email", "a@b.example.invalid", "s", "b")
        v.append("handoff transport delivered")
    except TransportDenied:
        pass
    return v


def opt_out_line_required(o):
    o.build()
    d = o.pending()[0]
    args = {k: d[k] for k in ("lead_id", "channel", "recipient", "subject", "personalization", "meta")}
    args.update(body=d["body"].replace(OPT_OUT_LINE, ""), template_id="zaipos_no_optout")
    out = o.h.cp.execute({"operation_id": "enq-noopt", "actor": ACTOR, "capability": "cap.outreach.enqueue",
                          "args": args})
    return [] if out.reason == "worker:outreach:no_opt_out_line" else [f"draft without opt-out: {out.reason}"]


CASES = [not_in_manifest, confirm_twice, cap_evasion, suppressed_after_opt_out, body_edited_after_plan,
         grant_controls, key_custody, handoff_needs_explicit_config, opt_out_line_required]


class HandoffAdversarial(unittest.TestCase):
    def test_zero_violations(self):
        bad = {}
        for case in CASES:
            o = H()
            try:
                v = case(o)
                if v:
                    bad[case.__name__] = v
            finally:
                o.close()
        print(f"\nHANDOFF_ADVERSARIAL cases={len(CASES)} violations={len(bad)}", file=sys.stderr)
        self.assertGreaterEqual(len(CASES), 8)
        self.assertEqual(bad, {})


class HandoffCli(unittest.TestCase):
    def test_end_to_end_cli(self):
        with tempfile.TemporaryDirectory() as t:
            st, out = os.path.join(t, "state"), os.path.join(t, "handoff")
            env = {**os.environ, "PYTHONPATH": SRC, "OUTREACH_APPROVAL_KEY": os.path.join(t, "keys", "a.key")}

            def run(*a):
                p = subprocess.run([sys.executable, "-m", "outreach", a[0], "--state", st, *a[1:]],
                                   capture_output=True, text=True, env=env, timeout=60)
                return p.returncode, json.loads(p.stdout) if p.stdout.strip() else p.stderr
            self.assertEqual(run("build", "--pipeline", os.path.join(FIX, "pipeline.csv"),
                                 "--omega", os.path.join(FIX, "omega.csv"))[0], 0)
            self.assertEqual(run("send-plan", "--grant", REF, "--out", out)[1]["skipped"], {"grant_missing": 8})
            rc, g = run("grant-standing", "--ref", REF, "--granted-on", "2026-10-20", "--max-sends", "9",
                        "--statement", "synthetic standing permission", "--granted-by", "user:test")
            self.assertEqual((rc, g["max_sends"]), (0, 9))
            rc, p = run("send-plan", "--grant", REF, "--out", out)
            self.assertEqual((rc, p["reserved"], p["legacy_approval_key_in_state"]), (0, 4, False))
            man = os.path.join(out, "manifest.json")
            rc, vm = run("verify-manifest", "--manifest", man)
            self.assertEqual((rc, vm["ok"]), (0, 4))
            with open(man, encoding="utf-8") as fh:
                rids = [e["reservation_id"] for e in json.load(fh)["entries"]]
            self.assertEqual(run("record-sent", "--reservation", rids[0], "--message-id", "18f00a")[0], 0)
            self.assertEqual(run("record-sent", "--reservation", rids[0], "--message-id", "18f00a")[1]["status"],
                             "replayed")
            self.assertEqual(run("record-sent", "--reservation", "rsv:" + "1" * 24, "--message-id", "x")[0], 2)
            self.assertEqual(run("record-failed", "--reservation", rids[1], "--reason", "gmail 500")[0], 0)
            with open(man, encoding="utf-8") as fh:
                rcpt = json.load(fh)["entries"][2]["recipient"]
            rf = os.path.join(t, "reply.txt")
            with open(rf, "w", encoding="utf-8") as fh:
                fh.write("Unsubscribe.\n")
            rc, r = run("record-reply", "--from", rcpt, "--text-file", rf, "--message-id", "reply-1")
            self.assertEqual((rc, r["result"]["opt_out"]), (0, True))
            rc, vm = run("verify-manifest", "--manifest", man)
            self.assertEqual((rc, vm["ok"]), (1, 1))
            rc, s = run("status")
            self.assertEqual((s["sends"], s["reservations"]), (1, {"sent": 1, "failed": 1, "cancelled": 1,
                                                                    "reserved": 1}))
            self.assertEqual(run("send-plan", "--grant", REF, "--out", out, "--approval-key",
                                 os.path.join(st, "approval.key"))[1], {"error": "approval_key_inside_state_dir"})
            with open(os.path.join(st, "audit.jsonl")) as fh:
                ev = [json.loads(x)["event"] for x in fh]
            print(f"HANDOFF_CLI reserved={p['reserved']} verified_ok=4 recorded_sent={s['sends']} "
                  f"opt_out={ev.count('outreach_reply')} transport_calls=0")


if __name__ == "__main__":
    unittest.main()
