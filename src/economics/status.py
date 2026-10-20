"""Operator status report + redacted JSONL export (cycle-009, D-0024). Read-only.

python -m economics status --config cfg.json [--format json|md] [--export-jsonl out.jsonl]
cfg: {"audit": path, "kill_switch": path,
      "scheduler": {"db", "key_file"}, "economics": {"db", "key_file", "caps": {...}},
      "memory": {"db", "key_file"}, "claims": {"db", "key_file"}}   (all but "audit" optional)
Keys are read from files, never from the command line. Exit 0 iff no attention items.
"""

import json
import os

from control_plane.audit import redact
from control_plane.canonical import canonical_json


def _key(spec: dict) -> bytes:
    with open(spec["key_file"], "rb") as fh:
        return fh.read()


def status_report(cfg: dict) -> dict:
    from control_plane import KillSwitch
    from integrity import crosscheck
    from .ledger import Caps, EconomicsLedger
    rep = {"kill_switch": {"engaged": KillSwitch(cfg["kill_switch"]).engaged() if cfg.get("kill_switch")
                           else "UNKNOWN"}, "attention": []}
    stores = {}
    if cfg.get("scheduler"):
        from scheduler import JobStore
        js = JobStore(cfg["scheduler"]["db"], _key(cfg["scheduler"]))
        jobs, runs = js.jobs(), js.runs()
        rep["scheduler"] = {
            "jobs": len(jobs),
            "quarantined_jobs": sorted({j for j, spec, _ in jobs if spec is None} |
                                       {r["job_id"] for r in runs if r["state"] == "quarantined"}),
            "in_doubt_runs": [{"job_id": r["job_id"], "slot": r["slot"], "reason": r["reason"]}
                              for r in runs if r["state"] == "in_doubt"],
            "running": [{"job_id": r["job_id"], "slot": r["slot"], "lease_until": r["lease_until"]}
                        for r in runs if r["state"] == "running"],
            "skipped_gaps": [{"job_id": j, **g} for j, _, _ in jobs for g in js.gaps(j)]}
        js.close()
    if cfg.get("economics"):
        e = cfg["economics"]
        stores["economics"] = EconomicsLedger(e["db"], _key(e), None, caps=Caps(**e.get("caps", {})))
        rep["budget"] = stores["economics"].burn(e.get("day"))
    for name in ("memory", "claims"):
        if cfg.get(name):
            if name == "memory":
                from memory import MemoryStore as S
            else:
                from research import ClaimLedger as S
            stores[name] = S(cfg[name]["db"], _key(cfg[name]))
    rep["crosscheck"] = crosscheck(cfg["audit"], stores)
    rep["attention"] = _attention(rep)
    return redact(rep)


def _attention(rep: dict) -> list[str]:
    out = []
    if rep["kill_switch"]["engaged"] is True:
        out.append("kill_switch_engaged")
    s = rep.get("scheduler", {})
    out += [f"in_doubt:{r['job_id']}@{r['slot']}" for r in s.get("in_doubt_runs", [])]
    out += [f"quarantined:{j}" for j in s.get("quarantined_jobs", [])]
    if s.get("skipped_gaps"):
        out.append(f"skipped_gaps:{sum(g['skipped'] for g in s['skipped_gaps'])}")
    b = rep.get("budget")
    if b:
        out += [f"reservation_without_outcome:{x}" for x in b["reservations_without_outcome"]]
        if b["actual_usd"]["unknown"]:
            out.append(f"actual_usd_unknown:{b['actual_usd']['unknown']}")
        cap = b["caps"].get("day_usd_global")
        if cap is not None and b["reserved_usd"]["known_sum"] >= 0.8 * cap:
            out.append("day_usd_global_burn>=80%")
    out += [f"crosscheck_failed:{n}" for n, st in sorted(rep["crosscheck"]["stores"].items()) if not st["ok"]]
    if not rep["crosscheck"]["audit_chain_ok"]:
        out.append("crosscheck_failed:audit")
    return out


def render_markdown(rep: dict) -> str:
    L = ["# Operator status", "", f"- kill switch engaged: **{rep['kill_switch']['engaged']}**",
         f"- attention items: **{len(rep['attention'])}**"]
    L += [f"  - `{a}`" for a in rep["attention"]]
    s = rep.get("scheduler")
    if s:
        L += ["", "## Scheduler", f"- jobs: {s['jobs']}", f"- quarantined: {s['quarantined_jobs'] or 'none'}",
              f"- in_doubt runs: {len(s['in_doubt_runs'])}", f"- running: {len(s['running'])}",
              f"- skipped gaps: {len(s['skipped_gaps'])}"]
        L += [f"  - {g['job_id']}: {g['skipped']} slot(s) after {g['after']} up to {g['upto']} ({g['policy']})"
              for g in s["skipped_gaps"]]
    b = rep.get("budget")
    if b:
        fmt = lambda t: f"{t['value']}" if t["value"] is not None else \
            f"UNKNOWN ({t['unknown']} unknown, known part {t['known_sum']})"
        L += ["", f"## Budget burn ({b['day']} UTC)", f"- caps: `{canonical_json(b['caps'])}`",
              f"- reserved (configured estimates) USD: {fmt(b['reserved_usd'])}",
              f"- actual USD: {fmt(b['actual_usd'])}", f"- actual revenue USD: {fmt(b['actual_revenue'])}",
              f"- wall s: {fmt(b['wall_s'])}; egress bytes: {fmt(b['egress_bytes'])}",
              f"- operations: {b['operations']}; model calls: {b['model_calls']}",
              f"- reservations without outcome: {len(b['reservations_without_outcome'])}"]
    c = rep["crosscheck"]
    L += ["", "## Cross-check", f"- audit chain ok: {c['audit_chain_ok']} ({c['audit_records']} records)"]
    L += [f"- {n}: ok={st['ok']} rows={st['rows']} anchors={st['anchors']} chain_ok={st['chain_ok']} "
          f"missing_or_altered={st['missing_or_altered']} unanchored={st['unanchored']}"
          for n, st in sorted(c["stores"].items())]
    return "\n".join(L) + "\n"


def export_jsonl(records, path: str) -> int:
    """Write one canonical JSON line per record, every string redacted. Returns the count."""
    n = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        for rec in records:
            fh.write(canonical_json(redact(rec)) + "\n")
            n += 1
    return n


def main(argv=None) -> int:
    import argparse
    import sys
    p = argparse.ArgumentParser(prog="economics")
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status")
    st.add_argument("--config", required=True)
    st.add_argument("--format", choices=("json", "md"), default="json")
    st.add_argument("--export-jsonl")
    a = p.parse_args(argv)
    with open(a.config) as fh:
        cfg = json.load(fh)
    rep = status_report(cfg)
    if a.export_jsonl:
        recs = [{"type": "status", **rep}]
        if cfg.get("economics"):
            from .ledger import EconomicsLedger
            led = EconomicsLedger(cfg["economics"]["db"], _key(cfg["economics"]), None)
            recs += [{"type": "economics_entry", **e} for e in led.entries()]
        export_jsonl(recs, a.export_jsonl)
    sys.stdout.write(json.dumps(rep, sort_keys=True, indent=1) + "\n" if a.format == "json" else render_markdown(rep))
    return 0 if not rep["attention"] else 1
