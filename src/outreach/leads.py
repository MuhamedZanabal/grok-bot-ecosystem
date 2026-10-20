"""Lead ingestion, sanitization, dedupe and rule scoring (cycle-010, D-0026).

Input CSVs are untrusted data. Only whitelisted columns are read; every value is cleaned
(control/bidi/zero-width characters removed, whitespace collapsed, length capped). Fields
that feed templates must be plain text; anything that looks like markup, a URL or an
instruction marks the lead `held` and it is never drafted. Every field keeps provenance
(source label, row, file sha256). Scores are rule sums, each rule with its evidence.
"""

import csv
import hashlib
import re
import unicodedata

_INVISIBLE = re.compile("[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
_SLOT_OK = set(" &'’.,-()°/#+!")
_INJECT = re.compile(r"(ignore|instruction|prompt|system|assistant|approve|token|password|send |forward|"
                     r"https?:|www\.|@|<|>|\{|\}|\$|`|\\|\[|\])", re.I)
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})+$")
SLOT_FIELDS = ("name", "area")   # raw fields a template may put into a message (category only via a fixed map)
MAXLEN = {"name": 60, "area": 40, "category": 30, "cuisine": 60, "street": 80, "email": 254, "phone": 20,
          "website": 300, "site_status": 20, "tech_signals": 120, "instagram": 120, "opening_hours": 120,
          "branches": 3, "status": 30, "touches": 3}
FOOD = {"restaurant", "cafe", "fast_food", "bakery", "coffee_shop", "ice_cream", "food_court"}
SUPPRESS_STATUS = {"BOUNCED": "bounced", "CLOSED_NO_REPLY": "closed_no_reply_after_2_touches"}
PRIOR_PENDING = {"GMAIL_DRAFT_READY", "DRAFTED_HELD"}


def clean(value, field: str) -> str:
    v = unicodedata.normalize("NFC", _INVISIBLE.sub(" ", str(value or "")))
    return " ".join(v.split())[: MAXLEN[field]]


def norm_email(e: str) -> str:
    """Case, whitespace, +tag and trailing dot normalized; '' if not a plausible address."""
    e = e.strip().strip(".").lower()
    if not EMAIL.fullmatch(e):
        return ""
    local, domain = e.split("@", 1)
    return f"{local.split('+', 1)[0]}@{domain.rstrip('.')}"


def _name_key(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def _row_fields(kind: str, row: dict) -> dict:
    if kind == "pipeline":
        return {"name": row.get("business"), "area": row.get("area"), "email": row.get("email"),
                "status": row.get("status"), "touches": row.get("followups_sent")}
    emails = [x for x in (row.get("email") or "", *(row.get("site_emails") or "").split(";")) if x.strip()]
    return {"name": row.get("name"), "area": row.get("area"), "category": row.get("category"),
            "cuisine": row.get("cuisine"), "street": row.get("street"), "phone": row.get("phone"),
            "email": emails[0] if emails else "", "website": row.get("website"),
            "site_status": row.get("site_status"), "tech_signals": row.get("tech_signals"),
            "instagram": row.get("instagram") or row.get("site_instagram"),
            "opening_hours": row.get("opening_hours"), "branches": row.get("branches_in_osm")}


def ingest(sources) -> tuple[list[dict], dict]:
    """sources: [(label, kind in {'pipeline','omega'}, path)]. Returns (leads, stats)."""
    recs = []
    for label, kind, path in sources:
        with open(path, "rb") as fh:
            raw = fh.read()
        sha = hashlib.sha256(raw).hexdigest()
        for i, row in enumerate(csv.DictReader(raw.decode("utf-8-sig").splitlines()), start=2):
            f = {k: clean(v, k) for k, v in _row_fields(kind, row).items() if v is not None}
            f = {k: v for k, v in f.items() if v}
            if f.get("email"):   # keep the address as written (if valid); normalize only for keys/caps
                f["email"] = f["email"].strip().strip(".") if norm_email(f["email"]) else ""
                f = {k: v for k, v in f.items() if v}
            if f.get("name"):
                recs.append({"fields": f, "src": {"source": label, "row": i, "file_sha256": sha}})
    # union-find over shared keys (email, phone digits, normalized name)
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, r in enumerate(recs):
        f = r["fields"]
        keys = [f"n:{_name_key(f['name'])}"] + ([f"e:{norm_email(f['email'])}"] if f.get("email") else []) + \
            ([f"p:{re.sub(r'[^0-9]', '', f['phone'])}"] if len(re.sub(r"[^0-9]", "", f.get("phone", ""))) >= 7 else [])
        r["keys"] = keys
        for k in keys:
            if k in seen:
                parent[find(i)] = find(seen[k])
            else:
                seen[k] = i
    groups = {}
    for i in range(len(recs)):
        groups.setdefault(find(i), []).append(recs[i])
    leads = [_merge(g) for g in groups.values()]
    leads.sort(key=lambda x: x["lead_id"])
    return leads, {"records": len(recs), "leads": len(leads), "merged": len(recs) - len(leads)}


def _merge(group: list[dict]) -> dict:
    fields, prov, conflicts = {}, {}, []
    for r in group:
        for k, v in r["fields"].items():
            if k not in fields:
                fields[k], prov[k] = v, r["src"]
            elif fields[k] != v and k not in ("status", "touches"):
                conflicts.append({"field": k, "kept": prov[k], "other": r["src"]})
            elif k in ("status", "touches") and r["src"]["source"] == "pipeline":
                fields[k], prov[k] = v, r["src"]
    key = min(min(r["keys"]) for r in group)
    lead = {"lead_id": "lead:" + hashlib.sha256(key.encode()).hexdigest()[:20], "fields": fields,
            "provenance": prov, "conflicts": conflicts, "sources": sorted({r["src"]["source"] for r in group})}
    lead["hold"] = _hold(fields)
    lead["suppress"] = SUPPRESS_STATUS.get(fields.get("status", ""))
    lead["prior_pending"] = fields.get("status") in PRIOR_PENDING
    return lead


def _hold(fields: dict) -> str | None:
    for k in SLOT_FIELDS:
        v = fields.get(k, "")
        if v and (_INJECT.search(v) or not all(ch.isalnum() or ch in _SLOT_OK for ch in v)):
            return f"unsafe_field:{k}"
    return None


RULES = (  # (rule_id, points, field, predicate, meaning)
    ("has_email", 3, "email", lambda v: bool(v), "public business email present"),
    ("has_phone", 1, "phone", lambda v: bool(v), "public phone present"),
    ("food_service", 2, "category", lambda v: v in FOOD, "food-service category (tables/KDS/delivery fit)"),
    ("multi_branch", 2, "branches", lambda v: v.isdigit() and int(v) >= 2, "2+ branches listed"),
    ("live_website", 1, "site_status", lambda v: v == "200", "own website responded 200"),
    ("social_only", 1, "site_status", lambda v: v == "social-only", "social-only web presence"),
    ("hours_listed", 1, "opening_hours", lambda v: bool(v), "opening hours listed"),
)


def score(lead: dict) -> dict:
    ev, total = [], 0
    for rid, pts, field, pred, meaning in RULES:
        v = lead["fields"].get(field, "")
        if v and pred(v):
            total += pts
            ev.append({"rule": rid, "points": pts, "field": field, "value": v, "meaning": meaning,
                       "source": lead["provenance"].get(field)})
    return {"score": total, "max": sum(r[1] for r in RULES), "evidence": ev}
