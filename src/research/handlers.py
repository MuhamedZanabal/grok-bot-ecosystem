"""Research capabilities (TrustedHostHandler: repository code, host side, no tool/model
code). All trust-relevant writes - source registration, lifecycle transitions, claims -
go through ControlPlane: policy, schema, idempotency and audit apply to each one."""

from control_plane import Capability, HandlerFailure, RiskLevel, TrustedHostHandler

from .sources import LEGAL, SourceError

REGISTER_CAP = "cap.research.source_register"
TRANSITION_CAP = "cap.research.source_transition"
CLAIM_CAP = "cap.research.claim_record"
_ID = {"type": "string", "pattern": "[a-z]{2,4}:[0-9a-f]{24,32}"}
_EVIDENCE = {"type": "object", "required": ["source_id", "record_id", "content_sha256", "quote"],
             "additionalProperties": False,
             "properties": {"source_id": _ID, "record_id": {"type": "string", "maxLength": 64},
                            "content_sha256": {"type": "string", "maxLength": 64},
                            "quote": {"type": "string", "maxLength": 300}}}
_WORD = {"type": "string", "pattern": "[A-Za-z0-9][A-Za-z0-9._-]{0,63}"}
REGISTER_SCHEMA = {"type": "object", "required": ["url", "kind"], "additionalProperties": False,
                   "properties": {"url": {"type": "string", "minLength": 1, "maxLength": 2048},
                                  "kind": {"type": "string", "enum": ["web_page"]}}}
TRANSITION_SCHEMA = {
    "type": "object", "required": ["source_id", "to", "reason", "evidence"], "additionalProperties": False,
    "properties": {"source_id": _ID, "to": {"type": "string", "enum": ["evaluating", "accepted", "rejected", "deferred"]},
                   "reason": {"type": "string", "minLength": 1, "maxLength": 200},
                   "evidence": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
                       "type": "object", "required": ["kind", "id"], "additionalProperties": False,
                       "properties": {"kind": {"type": "string", "enum": ["record", "claim"]},
                                      "id": {"type": "string", "maxLength": 64},
                                      "content_sha256": {"type": "string", "maxLength": 64}}}}}}
CLAIM_SCHEMA = {
    "type": "object", "required": ["branch_id", "subject", "attribute", "value", "origin", "evidence"],
    "additionalProperties": False,
    "properties": {"branch_id": _ID, "subject": _WORD, "attribute": _WORD, "value": _WORD,
                   "origin": {"type": "string", "enum": ["tier0", "model"]},
                   "evidence": {"type": "array", "minItems": 1, "maxItems": 8, "items": _EVIDENCE}}}


class _Host(TrustedHostHandler):
    __slots__ = ("_ctx",)

    def __init__(self, ctx) -> None:
        object.__setattr__(self, "_ctx", ctx)

    def __setattr__(self, name, value) -> None:
        raise AttributeError("immutable")


class RegisterHandler(_Host):
    __slots__ = ()

    def __call__(self, args):
        c = self._ctx
        try:
            row, created = c.sources.register(args["url"], args["kind"], c.clock())
        except SourceError as exc:
            raise HandlerFailure(f"research:{exc.code}") from None
        if created:
            c.audit.append("source_registered", source_id=row["source_id"], status=row["status"],
                           trusted=row["trusted"], execution_allowed=row["execution_allowed"])
        return {k: row[k] for k in ("source_id", "url", "status", "trusted", "execution_allowed")} | {"created": created}


class TransitionHandler(_Host):
    __slots__ = ()

    def _check(self, src, to, evidence) -> str | None:
        c = self._ctx
        verified_claim = False
        for ev in evidence:
            if ev["kind"] == "record":
                row, why = c.memory.get(ev["id"])
                if row is None:
                    return f"evidence_{why}"
                if row["content_sha256"] != ev.get("content_sha256"):
                    return "evidence_hash_mismatch"
                if row["origin_url"] != src["url"]:
                    return "evidence_origin_mismatch"
            else:
                claim = c.ledger.get(ev["id"])
                if claim is None or src["source_id"] not in {p["source_id"] for p in claim["evidence"]}:
                    return "evidence_claim_not_for_source"
                verified_claim = verified_claim or c.verifier.verdict(claim)[0] == "verified"
        if to == "accepted" and not verified_claim:
            return "accept_requires_verified_claim"
        if to in ("evaluating",) and not any(e["kind"] == "record" for e in evidence):
            return "evaluating_requires_record"
        return None

    def __call__(self, args):
        c = self._ctx
        src = c.sources.get(args["source_id"])
        if src is None:
            raise HandlerFailure("research:unknown_source")
        legal = (src["status"], args["to"]) in LEGAL
        bad = self._check(src, args["to"], args["evidence"]) if legal else "illegal_transition"
        try:
            if bad:
                raise SourceError(bad)
            out = c.sources.transition(src["source_id"], args["to"], args["reason"], args["evidence"], c.clock())
        except SourceError as exc:
            c.audit.append("source_transition_refused", source_id=src["source_id"], frm=src["status"],
                           to=args["to"], reason=exc.code)
            raise HandlerFailure(f"research:{exc.code}") from None
        c.audit.append("source_transition", source_id=src["source_id"], frm=out["from"], to=out["to"],
                       reason=args["reason"], evidence=args["evidence"])
        return out


class ClaimHandler(_Host):
    __slots__ = ()

    def __call__(self, args):
        c = self._ctx
        claim = {k: args[k] for k in ("branch_id", "subject", "attribute", "value", "evidence")}
        if c.ledger.branch(args["branch_id"]) is None:
            raise HandlerFailure("research:unknown_branch")
        refused = c.verifier.admit(claim)
        if refused:
            c.audit.append("claim_refused", branch_id=args["branch_id"], attribute=args["attribute"],
                           origin=args["origin"], reason=refused)
            raise HandlerFailure(f"research:{refused}")
        cid, created = c.ledger.add(args["branch_id"], args["subject"], args["attribute"], args["value"],
                                    args["origin"], args["evidence"], c.clock())
        verdict, reasons = c.verifier.verdict(c.ledger.get(cid))
        c.audit.append("claim_recorded", claim_id=cid, created=created, origin=args["origin"], verdict=verdict,
                       sources=sorted({p["source_id"] for p in args["evidence"]}))
        return {"claim_id": cid, "verdict": verdict, "reasons": reasons}


def register_research(policy, control_plane, ctx) -> None:
    for cap, desc, schema, h in (
            (REGISTER_CAP, "Register a source as candidate (untrusted, not executable)", REGISTER_SCHEMA,
             RegisterHandler),
            (TRANSITION_CAP, "Move a source through its lifecycle with recorded evidence", TRANSITION_SCHEMA,
             TransitionHandler),
            (CLAIM_CAP, "Record a claim whose evidence pointers pass the verifier", CLAIM_SCHEMA, ClaimHandler)):
        policy.register(Capability(cap, RiskLevel.LOCAL_WRITE, desc, schema))
        control_plane.bind(cap, h(ctx))
