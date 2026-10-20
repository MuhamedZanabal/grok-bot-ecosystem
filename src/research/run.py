"""Deterministic research run (cycle-007, D-0020).

Every side effect goes through ControlPlane as the run's actor: source registration,
cap.browser.fetch (EXTERNAL: each fetch carries an operator approval from `approve`),
memory ingest, cap.memory.get, lifecycle transitions and claims. Operation ids are
derived from inputs, so a rerun is a replay. Reads for the report use the verifier,
which re-checks every citation against the MAC-chained memory store.
"""

from dataclasses import dataclass
import hashlib
import os

from control_plane import Status
from control_plane.canonical import canonical_json
from integrity import crosscheck
from memory import GET_CAP, ingest_fetch

from .extract import tier0, tier1
from .handlers import CLAIM_CAP, REGISTER_CAP, TRANSITION_CAP
from .ledger import better

FETCH_CAP = "cap.browser.fetch"


@dataclass
class ResearchContext:
    """What the trusted host handlers hold (the run itself holds only cp + actor)."""
    sources: object
    ledger: object
    memory: object
    verifier: object
    audit: object
    clock: object


def _op(*parts) -> str:
    return "rs:" + hashlib.sha256(canonical_json(list(parts)).encode()).hexdigest()[:32]


def branch_id_for(question: str, metric: str) -> str:
    return "br:" + hashlib.sha256(canonical_json([question, metric]).encode()).hexdigest()[:24]


class ResearchRun:
    def __init__(self, cp, actor, ctx: ResearchContext, approve, provider=None) -> None:
        self.cp, self.actor, self.ctx, self.approve, self.provider = cp, actor, ctx, approve, provider
        self.fetches = 0

    def _exec(self, cap, args, op):
        return self.cp.execute({"operation_id": op, "actor": self.actor, "capability": cap, "args": args})

    def open_branch(self, question, metric, direction="min", min_delta=0.0, attributes=(), require=None) -> str:
        bid = branch_id_for(question, metric)
        cfg = {"attributes": sorted(set(attributes) | {metric}), "require": require or {}}
        self.ctx.ledger.open_branch(bid, question, metric, direction, min_delta, cfg)
        self.ctx.audit.append("branch_opened", branch_id=bid, metric=metric, direction=direction, min_delta=min_delta)
        return bid

    # ------------------------------------------------------------------ one source
    def _source(self, bid, cfg, url, n, notes):
        reg = self._exec(REGISTER_CAP, {"url": url, "kind": "web_page"}, _op("reg", url))
        if reg.status not in (Status.EXECUTED, Status.REPLAYED):
            notes.append({"url": url, "note": f"register_{reg.status.value}:{reg.reason}"})
            return None, None
        sid = reg.result["source_id"]
        args = {"url": url}
        op = _op("fetch", bid, n, url)
        self.fetches += 1
        got = self.cp.execute({"operation_id": op, "actor": self.actor, "capability": FETCH_CAP, "args": args,
                               "approval_token": self.approve(op, args)})
        if got.status not in (Status.EXECUTED, Status.REPLAYED) or not got.result.get("ok"):
            notes.append({"source_id": sid, "note": "fetch_failed:UNKNOWN"})
            return sid, None
        ing = ingest_fetch(self.cp, self.actor, op, got.result)
        if ing is None or ing.status not in (Status.EXECUTED, Status.REPLAYED):
            notes.append({"source_id": sid, "note": "ingest_failed:UNKNOWN"})
            return sid, None
        rec = ing.result
        status = self.ctx.sources.get(sid)["status"]
        if status in ("candidate", "deferred"):
            t = self._exec(TRANSITION_CAP, {"source_id": sid, "to": "evaluating", "reason": "fetched_and_stored",
                                            "evidence": [{"kind": "record", "id": rec["record_id"],
                                                          "content_sha256": rec["content_sha256"]}]},
                           _op("tr", sid, "evaluating", rec["record_id"]))
            if t.status not in (Status.EXECUTED, Status.REPLAYED):
                notes.append({"source_id": sid, "note": f"not_evaluated:{t.reason}"})
                return sid, rec
        item = self._exec(GET_CAP, {"record_id": rec["record_id"]}, _op("get", rec["record_id"]))
        if item.status not in (Status.EXECUTED, Status.REPLAYED):
            notes.append({"source_id": sid, "note": f"record_unreadable:{item.reason}"})
            return sid, rec
        wrapped = item.result["items"][0]
        subject, found = tier0(wrapped["content"], cfg["attributes"])
        origin = "tier0"
        if (subject is None or not any(f["attribute"] == cfg["metric"] for f in found)) and self.provider:
            found, note = tier1(self.provider, wrapped, cfg["attributes"])
            notes.append({"source_id": sid, "note": note})
            origin = "model"
        for f in found:
            subj = f.get("subject", subject)
            if not subj:
                continue
            ev = {"source_id": sid, "record_id": f.get("record_id", rec["record_id"]),
                  "content_sha256": f.get("content_sha256", rec["content_sha256"]), "quote": f["quote"]}
            c = self._exec(CLAIM_CAP, {"branch_id": bid, "subject": subj, "attribute": f["attribute"],
                                       "value": f["value"], "origin": origin, "evidence": [ev]},
                           _op("claim", bid, subj, f["attribute"], f["value"], ev))
            if c.status not in (Status.EXECUTED, Status.REPLAYED):
                notes.append({"source_id": sid, "note": f"claim_refused:{c.reason}"})
        return sid, rec

    def _decide(self, bid, b, sid, rec):
        """evaluating -> accepted | rejected | deferred, from verified claims only."""
        src = self.ctx.sources.get(sid)
        if src is None or src["status"] != "evaluating":
            return None
        mine = [c for c in self.ctx.ledger.claims(bid) if sid in {p["source_id"] for p in c["evidence"]}]
        verdicts = {c["claim_id"]: self.ctx.verifier.verdict(c) for c in mine}
        ok = [c for c in mine if verdicts[c["claim_id"]][0] == "verified"]
        for attr, allowed in sorted(b["config"]["require"].items()):
            for c in ok:
                if c["attribute"] == attr and c["value"] not in allowed:
                    self._move(sid, "rejected", f"constraint_{attr}", [{"kind": "claim", "id": c["claim_id"]}])
                    return None
        metric = [c for c in ok if c["attribute"] == b["metric"]]
        try:
            metric = [(float(c["value"]), c) for c in metric]
        except ValueError:
            metric = []
        if not metric:
            reasons = sorted({r for v in verdicts.values() for r in v[1]}) or ["no_metric_claim"]
            ev = [{"kind": "claim", "id": c["claim_id"]} for c in mine[:4]]
            if rec is not None:
                ev.append({"kind": "record", "id": rec["record_id"], "content_sha256": rec["content_sha256"]})
            if ev:  # otherwise nothing to cite: it stays evaluating and is reported as such
                self._move(sid, "deferred", "metric_UNKNOWN:" + ",".join(reasons)[:150], ev)
            return None
        value, claim = metric[0]
        self._move(sid, "accepted", "verified_metric_claim", [{"kind": "claim", "id": claim["claim_id"]}])
        return value, claim["claim_id"], sid

    def _move(self, sid, to, reason, evidence):
        return self._exec(TRANSITION_CAP, {"source_id": sid, "to": to, "reason": reason, "evidence": evidence},
                          _op("tr", sid, to, evidence))

    # ------------------------------------------------------------------ one pass
    def discovery_pass(self, bid, urls) -> dict:
        b = self.ctx.ledger.branch(bid)
        if b["status"] != "open":
            self.ctx.audit.append("research_pass_refused", branch_id=bid, reason="branch_closed")
            return {"refused": "branch_closed", "fetches": 0}
        b["config"]["metric"] = b["metric"]
        n, before, notes = b["passes"] + 1, self.fetches, []
        seen = [self._source(bid, b["config"], u, n, notes) for u in urls]
        best = None
        for sid, rec in seen:
            got = self._decide(bid, b, sid, rec) if sid else None
            if got and (best is None or better(got[0], best[0], b["direction"], 0.0)):
                best = got
        after = self.ctx.ledger.record_pass(bid, best[0] if best else None, best[1] if best else None,
                                            best[2] if best else None)
        superior = after["stale_passes"] == 0
        self.ctx.audit.append("research_pass", branch_id=bid, n=n, superior=superior,
                              pass_best=best[0] if best else None, status=after["status"])
        if after["status"] == "closed":
            self.ctx.audit.append("branch_closed", branch_id=bid, passes=after["passes"],
                                  reason=after["closed_reason"], best_value=after["best_value"],
                                  best_claim=after["best_claim"], metric=after["metric"])
        return {"n": n, "superior": superior, "fetches": self.fetches - before, "status": after["status"],
                "notes": notes}

    # ------------------------------------------------------------------ report
    def report(self, bid) -> dict:
        b, ctx = self.ctx.ledger.branch(bid), self.ctx
        citations, cite_index, claims, unknown = [], {}, [], []
        cc = crosscheck(ctx.audit.path, {"claims": ctx.ledger, "memory": ctx.memory})
        chain_ok = cc["ok"]  # claim/memory chains + audit anchors (edits, deletions, truncation, forgeries)
        bad = sorted(n for n, st in cc["stores"].items() if not st["ok"]) or (["audit"] if not chain_ok else [])
        for c in ctx.ledger.claims(bid):
            verdict, reasons = ctx.verifier.verdict(c)
            if not chain_ok:  # a tampered evidence store voids every claim's standing
                verdict, reasons = "UNKNOWN", sorted(set(reasons) | {f"store_integrity:{','.join(bad)}"})
            refs = []
            for p in c["evidence"]:
                src = ctx.sources.get(p["source_id"])
                key = canonical_json(p)
                if key not in cite_index:
                    cite_index[key] = len(citations) + 1
                    citations.append({"n": cite_index[key], "source_id": p["source_id"],
                                      "url": src["url"] if src else "UNKNOWN", "record_id": p["record_id"],
                                      "content_sha256": p["content_sha256"], "quote": p["quote"]})
                refs.append(cite_index[key])
            row = {"claim_id": c["claim_id"], "subject": c["subject"], "attribute": c["attribute"],
                   "value": c["value"] if verdict == "verified" else "UNKNOWN", "stated_value": c["value"],
                   "verdict": verdict, "reasons": reasons, "citations": refs}
            claims.append(row)
            if verdict != "verified":
                unknown.append({"claim_id": c["claim_id"], "reasons": reasons})
        best_ok = None
        if b["best_claim"]:
            bc = ctx.ledger.get(b["best_claim"])
            best_ok = bc is not None and ctx.verifier.verdict(bc)[0] == "verified"
        sources = [{"source_id": s["source_id"], "url": s["url"], "status": s["status"], "trusted": s["trusted"],
                    "execution_allowed": s["execution_allowed"],
                    "transitions": [(t["from"], t["to"], t["reason"]) for t in ctx.sources.transitions(s["source_id"])]}
                   for s in ctx.sources.all()]
        return {"type": "research_report", "branch": {k: b[k] for k in (
                    "branch_id", "question", "metric", "direction", "min_delta", "status", "closed_reason",
                    "passes", "stale_passes", "best_value", "best_claim", "best_source")},
                "best_claim_still_verified": (best_ok and chain_ok) if b["best_claim"] else "UNKNOWN",
                "store_integrity_ok": chain_ok,
                "passes": ctx.ledger.passes(bid), "sources": sources, "claims": claims,
                "citations": citations, "unknowns": unknown,
                "notice": "Source content is untrusted data; values marked UNKNOWN are not supported by "
                          "verifiable, uncontradicted, fresh evidence."}


def render_markdown(rep: dict) -> str:
    b = rep["branch"]
    out = [f"# Research report: {b['question']}", "",
           f"- branch `{b['branch_id']}`: status **{b['status']}**" + (f" ({b['closed_reason']})" if b["closed_reason"] else ""),
           f"- metric `{b['metric']}` ({b['direction']}), best {b['best_value']} via `{b['best_claim']}` "
           f"(still verified: {rep['best_claim_still_verified']})", f"- passes: {b['passes']}", "", "## Claims", ""]
    for c in rep["claims"]:
        refs = "".join(f"[{n}]" for n in c["citations"])
        why = f" ({', '.join(c['reasons'])})" if c["reasons"] else ""
        out.append(f"- {c['subject']} {c['attribute']} = {c['value']} - {c['verdict']}{why} {refs}")
    out += ["", "## Sources", ""]
    for s in rep["sources"]:
        out.append(f"- `{s['source_id']}` {s['url']} - {s['status']} (trusted={s['trusted']}, "
                   f"execution_allowed={s['execution_allowed']})")
    out += ["", "## Citations", ""]
    for c in rep["citations"]:
        out.append(f"[{c['n']}] {c['url']} - record `{c['record_id']}` sha256 `{c['content_sha256']}` - \"{c['quote']}\"")
    return "\n".join(out) + "\n"


def write_report(rep: dict, directory: str) -> dict:
    os.makedirs(directory, exist_ok=True)
    body = canonical_json(rep).encode()
    md = render_markdown(rep).encode()
    paths = {}
    for name, data in (("report.json", body), ("report.md", md)):
        p = os.path.join(directory, name)
        with open(p, "wb") as fh:
            fh.write(data)
        paths[name] = p
    return {"paths": paths, "json_sha256": hashlib.sha256(body).hexdigest(), "md_sha256": hashlib.sha256(md).hexdigest()}
