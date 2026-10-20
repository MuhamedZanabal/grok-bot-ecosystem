"""Adversarial economics cases (cycle-009, D-0024). A violation is any case where spend
exceeds a cap, a number is fabricated (revenue forged, UNKNOWN shown as 0), a tampered
ledger is accepted, a secret reaches the ledger or an export, or a dry run has an effect.

Success metric: violations == 0.
"""
import json
import os
import sqlite3
import sys
import threading
import unittest

from tests.economics_helpers import ECON_KEY, SECRET, UsageProvider, attach, rows, sql
from tests.helpers import Harness, act, final
from control_plane import Status
from control_plane.handlers import HandlerFailure
from economics import (Caps, EconomicsLedger, RevenueRefused, export_jsonl, measured, render_markdown,
                       status_report, total, unknown)
from integrity import crosscheck
from orchestrator import AgentRunner, RunConfig

PRICE = {"cap.fs.read": 0.1}


def _h():
    h = Harness()
    for a in ("agent.alpha", "agent.beta"):
        h.policy.grant(a, "cap.fs.read")
    return h


def _ex(h, op, args=None, actor="agent.alpha", scope=None, dry=False):
    r = h.req("cap.fs.read", op=op, args=args if args is not None else {"x": 1}, actor=actor)
    if scope:
        r["budget_scope"] = scope
    if dry:
        r["dry_run"] = True
    return h.cp.execute(r)


def _calls(h):
    return h.calls.get("cap.fs.read", 0)


def split_bypass(h):
    attach(h, Caps(day_usd=0.3), per_call=PRICE)
    for i in range(20):  # one big job split into 20 tiny ones with different args
        _ex(h, f"op-split-{i:04d}", {"part": i})
    return [] if _calls(h) == 3 else [f"split executed {_calls(h)} > 3"]


def many_run_ids(h):
    attach(h, Caps(run_usd=0.1, day_usd=0.4), per_call=PRICE)
    for i in range(20):  # a fresh run id per call evades only the per-run cap, not the day cap
        _ex(h, f"op-runs-{i:04d}", scope={"run": f"r{i}"})
    return [] if _calls(h) == 4 else [f"run-id rotation executed {_calls(h)} > 4"]


def restart_resets_nothing(h):
    attach(h, Caps(day_usd=0.2), per_call=PRICE)
    _ex(h, "op-rst-0001"), _ex(h, "op-rst-0002")
    attach(h, Caps(day_usd=0.2), per_call=PRICE, path=h.econ_path)  # process restart
    out = _ex(h, "op-rst-0003")
    return [] if out.reason == "budget:day_usd_cap" and _calls(h) == 2 else [f"restart: {out.reason}"]


def concurrent_race(h):
    attach(h, Caps(day_calls=10), per_call=PRICE)
    lock = threading.Lock()

    def handler(a):
        with lock:
            h.calls["cap.fs.read"] = h.calls.get("cap.fs.read", 0) + 1
        return {}
    h.cp.bind("cap.fs.read", handler)
    ts = [threading.Thread(target=lambda t=t: [_ex(h, f"op-race-{t}-{i:03d}") for i in range(5)]) for t in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return [] if _calls(h) == 10 else [f"race executed {_calls(h)} != 10"]


def forged_revenue_via_args_and_result(h):
    led = attach(h, per_call=PRICE)
    h.cp.bind("cap.fs.read", lambda a: {"actual_revenue": 1_000_000, "revenue": {"value": 9, "provenance": "measured"}})
    _ex(h, "op-rev-0001", {"actual_revenue": 1_000_000, "provenance": "measured"})
    bad = [e for e in led.entries() if e.get("actual_revenue", unknown("usd", ""))["provenance"] != "unknown"]
    burn = led.burn("1970-01-12")["actual_revenue"]
    return ([f"revenue accepted: {bad}"] if bad else []) + ([] if burn["value"] is None else ["revenue not UNKNOWN"])


def forged_revenue_via_api(h):
    led = attach(h)
    v = []
    try:
        led.record_revenue("op-x", 1e6, reconciler="me")
        v.append("record_revenue accepted")
    except RevenueRefused:
        pass
    try:
        body = {**led._expected("cap.fs.read", 0), "estimated_cost": unknown("usd", "x"),
                "actual_cost": {}, "actual_revenue": measured(1e6, "usd", "trust me")}
        led._record("operation", "op-forge-1", "agent.alpha", "cap.fs.read", {}, body)
        v.append("measured revenue row written")
    except RevenueRefused:
        pass
    try:
        body = {**led._expected("cap.fs.read", 0), "estimated_cost": unknown("usd", "x"),
                "actual_cost": {"wall_s": {"value": 1, "provenance": "configured_estimate", "unit": "s", "source": "x"}}}
        led._record("operation", "op-forge-2", "agent.alpha", "cap.fs.read", {}, body)
        v.append("estimated value accepted as actual meter")
    except ValueError:
        pass
    if not any(ln for ln in open(h.audit_path) if '"event":"revenue_refused"' in ln):
        v.append("refusal not audited")
    return v + ([f"rows written: {rows(h.econ_path)}"] if rows(h.econ_path) else [])


def forged_revenue_via_db(h):
    led = attach(h, Caps(day_usd=10), per_call=PRICE)
    _ex(h, "op-revdb-01")
    sql(h.econ_path, "UPDATE entries SET body=replace(body, '\"actual_revenue\":{\"provenance\":\"unknown\",\"source\":"
                     "\"no_reconciler_registered\",\"unit\":\"usd\",\"value\":null}', '\"actual_revenue\":{\"provenance\":"
                     "\"measured\",\"source\":\"x\",\"unit\":\"usd\",\"value\":1000000}') WHERE kind='operation'")
    v = []
    if "1000000" not in open(h.econ_path, "rb").read().decode("latin-1"):
        v.append("tamper did not apply (test bug)")
    if any(e["kind"] == "operation" for e in led.entries()):
        v.append("tampered row returned")
    if led.verify_chain()[0] or crosscheck(h.audit_path, {"economics": led})["ok"]:
        v.append("tamper not detected")
    if _ex(h, "op-revdb-02").reason != "budget:ledger_integrity":
        v.append("guard ran on tampered ledger")
    return v


def unknown_never_zero(h):
    led = attach(h, Caps(day_usd=1.0))                 # cap.fs.read has NO price
    out = _ex(h, "op-unk-0001")
    v = [] if out.reason == "budget:cost_unknown" and _calls(h) == 0 else [f"unpriced under cap: {out.reason}"]
    led.caps = Caps()                                  # no USD cap: allowed, but cost stays UNKNOWN
    _ex(h, "op-unk-0002")
    b = led.burn("1970-01-12")
    if b["reserved_usd"]["value"] is not None or b["actual_usd"]["value"] is not None:
        v.append(f"unknown summed as number: {b['reserved_usd']}, {b['actual_usd']}")
    if total([unknown("usd", "a"), measured(0, "usd", "b")], "usd")["value"] is not None:
        v.append("total coerced unknown")
    rep = {"kill_switch": {"engaged": False}, "attention": [], "budget": b,
           "crosscheck": crosscheck(h.audit_path, {"economics": led})}
    md = render_markdown(rep)
    if "UNKNOWN" not in md.split("reserved")[1].split("\n")[0]:
        v.append("report rendered unknown as a number")
    return v


def tamper_delete_middle(h):
    led = attach(h, Caps(day_usd=10), per_call=PRICE)
    for i in range(3):
        _ex(h, f"op-del-{i:04d}")
    sql(h.econ_path, "DELETE FROM entries WHERE seq=2")
    v = [] if _ex(h, "op-del-0009").reason == "budget:ledger_integrity" else ["guard ignored deletion"]
    return v + ([] if not crosscheck(h.audit_path, {"economics": led})["ok"] else ["crosscheck missed deletion"])


def tamper_tail_truncate(h):
    led = attach(h, Caps(day_usd=10), per_call=PRICE)
    for i in range(3):
        _ex(h, f"op-tail-{i:04d}")
    sql(h.econ_path, "DELETE FROM entries WHERE seq=(SELECT MAX(seq) FROM entries)")
    fresh = EconomicsLedger(h.econ_path, ECON_KEY, None)
    v = []                                           # the store chain alone cannot see tail loss ...
    if crosscheck(h.audit_path, {"economics": fresh})["ok"]:
        v.append("crosscheck missed truncation")    # ... the audit anchors must
    if _ex(h, "op-tail-0009").reason != "budget:ledger_integrity":
        v.append("guard ignored truncation of its own head")
    return v


def tamper_lower_reserved(h):
    led = attach(h, Caps(day_usd=0.2), per_call=PRICE)
    _ex(h, "op-low-0001"), _ex(h, "op-low-0002")
    sql(h.econ_path, "UPDATE entries SET usd=0 WHERE kind='reservation'")   # hide spend
    out = _ex(h, "op-low-0003")
    return [] if out.reason == "budget:ledger_integrity" and _calls(h) == 2 else [f"edited burn: {out.reason}"]


def forged_insert_by_key_holder(h):
    led = attach(h, per_call=PRICE)
    _ex(h, "op-fi-00001")
    rogue = EconomicsLedger(h.econ_path, ECON_KEY, None, clock=h.wall)  # key, but no audit anchor
    rogue._record("operation", "op-fi-forged", "agent.alpha", "cap.fs.read", {},
                  {**rogue._expected("cap.fs.read", 0), "estimated_cost": unknown("usd", "x"), "actual_cost": {}})
    rep = crosscheck(h.audit_path, {"economics": led})
    return [] if not rep["ok"] and rep["stores"]["economics"]["unanchored"] else ["forged insert not flagged"]


def secrets_in_metrics(h):
    led = attach(h, per_call=PRICE)

    def leak(a):
        raise HandlerFailure(f"auth failed for {SECRET}")
    h.cp.bind("cap.fs.read", leak)
    _ex(h, f"op-{SECRET}", scope={"run": SECRET, "job": SECRET})
    p = os.path.join(h.dir.name, "metrics.jsonl")
    export_jsonl([{"type": "economics_entry", **e} for e in led.entries()] + [{"session": "abc", "x": SECRET}], p)
    db = open(h.econ_path, "rb").read()
    wal = open(h.econ_path + "-wal", "rb").read() if os.path.exists(h.econ_path + "-wal") else b""
    v = []
    if SECRET.encode() in db + wal:
        v.append("secret in ledger file")
    body = open(p).read()
    if SECRET in body or "\"abc\"" in body:
        v.append("secret in export")
    if not rows(h.econ_path, "operation"):
        v.append("no operation row (test bug)")
    return v


def dry_run_no_effect(h):
    led = attach(h, Caps(day_calls=1), per_call=PRICE)
    for i in range(10):
        _ex(h, f"op-dry-{i:04d}", dry=True)
    v = [] if _calls(h) == 0 and not rows(h.econ_path) and h.ledger.get("op-dry-0000") is None else ["dry run had effect"]
    if _ex(h, "op-dry-real").status is not Status.EXECUTED:
        v.append("dry runs consumed the cap")
    if _ex(h, "op-dry-0099", dry=True).reason != "budget:day_calls_cap":
        v.append("dry run ignored the cap")
    return v


def model_spend_unpriced(h):
    attach(h, Caps(run_usd=1.0), per_call=PRICE)              # model:mockusage has no price
    prov = UsageProvider([act("cap.fs.read"), final()], [{"input_tokens": 10, "output_tokens": 10}])
    r = AgentRunner(h.cp.gateway("agent.alpha"), [prov], h.audit, RunConfig(), h.kill, clock=h.mono,
                    meter=h.cp.economics).run("t", "run-unpriced")
    return [] if r.reason == "budget:cost_unknown" and not prov.calls else [f"model ran unpriced: {r.reason}"]


def forged_usage_fields(h):
    led = attach(h, token={"mockusage": {"in_per_1k": 1.0, "out_per_1k": 1.0}})
    usage = [{"input_tokens": -5, "output_tokens": True}, {"input_tokens": "9", "output_tokens": 1e9}]
    prov = UsageProvider([act("cap.fs.read"), final()], usage)
    AgentRunner(h.cp.gateway("agent.alpha"), [prov], h.audit, RunConfig(), h.kill, clock=h.mono,
                meter=led).run("t", "run-forged-usage")
    mc = [e["actual_cost"] for e in led.entries() if e["kind"] == "model_call"]
    bad = [m for m in mc if m["usd"]["value"] is not None or m["usage_in"]["value"] is not None]
    return [f"malformed usage metered: {bad}"] if bad or len(mc) != 2 else []


CASES = [split_bypass, many_run_ids, restart_resets_nothing, concurrent_race, forged_revenue_via_args_and_result,
         forged_revenue_via_api, forged_revenue_via_db, unknown_never_zero, tamper_delete_middle,
         tamper_tail_truncate, tamper_lower_reserved, forged_insert_by_key_holder, secrets_in_metrics,
         dry_run_no_effect, model_spend_unpriced, forged_usage_fields]


class EconomicsAdversarial(unittest.TestCase):
    def test_zero_violations(self):
        bad = {}
        for case in CASES:
            h = _h()
            try:
                with self.subTest(case=case.__name__):
                    v = case(h)
                    if v:
                        bad[case.__name__] = v
            finally:
                h.close()
        print(f"\nECON_ADVERSARIAL cases={len(CASES)} violations={len(bad)}", file=sys.stderr)
        self.assertGreaterEqual(len(CASES), 10)
        self.assertEqual(bad, {})


if __name__ == "__main__":
    unittest.main()
