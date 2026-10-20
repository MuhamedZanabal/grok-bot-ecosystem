"""Template drafting (cycle-010, D-0026). No model: repo-owned templates whose slots are
filled only from named lead fields; a clause whose field is missing is omitted. Each draft
carries its slot -> field -> value -> source map, and re-rendering from that map must
reproduce the body byte-for-byte (tests check this). Offer facts are the operator's own
product terms (revops_report.md), never facts about the lead."""

import hashlib

from control_plane.canonical import canonical_json

TEMPLATE_ID = "zaipos_email_v1"
CATEGORY_PHRASE = {"restaurant": "restaurant", "cafe": "café", "fast_food": "fast-food spot", "bakery": "bakery",
                   "coffee_shop": "coffee shop", "ice_cream": "dessert shop", "food_court": "food-court outlet"}
OFFER = ("I'm taking on a few founding businesses. Setup is free, you run it next to your current till for "
         "30 days, then it's BHD 12 a month per branch. If it's not useful, you stop and owe nothing.")
INTRO = ("I'm Zanabal, a developer based in Bahrain. I built ZAIPOS, a point-of-sale made for Bahrain: BHD to "
         "3 decimals, 10% VAT receipts, Cash/Card/BenefitPay, table orders, and delivery-app orders tracked net "
         "of commission.")
OPT_OUT_LINE = "If you'd rather not hear from me again, reply \"stop\" and I won't write again."   # required (cycle-011)
ASK = ("Could I stop by for 10 minutes this week? A time that suits you, or a quick \"no thanks\", is "
       "completely fine. " + OPT_OUT_LINE)
SIGNATURE = "Zanabal Muhamed"


def render(values: dict) -> tuple[str, str]:
    """values: slot -> text (slots: name [required], area, category_phrase, multi_branch)."""
    name, area = values["name"], values.get("area")
    subject = f"10-minute POS demo for {name}" + (f" ({area})" if area else "")
    parts = [f"Hi {name} team,", INTRO]
    if values.get("category_phrase"):
        parts.append(f"For a {values['category_phrase']} like {name}, table orders and delivery-app tracking "
                     "are usually where ZAIPOS saves the most time.")
    if values.get("multi_branch"):
        parts.append("Since more than one branch is listed for you, you'd also get per-branch daily reports "
                     "in one place.")
    parts += [OFFER, ASK, SIGNATURE]
    return subject, "\n\n".join(parts) + "\n"


def draft_hash(channel: str, recipient: str, subject: str, body: str) -> str:
    return hashlib.sha256(canonical_json({"channel": channel, "recipient": recipient, "subject": subject,
                                          "body": body}).encode()).hexdigest()


def make_draft(lead: dict, scored: dict) -> dict | None:
    """None unless the lead is draftable: email present, not held/suppressed/prior-pending."""
    f, prov = lead["fields"], lead["provenance"]
    if not f.get("email") or not f.get("name") or lead["hold"] or lead["suppress"] or lead["prior_pending"]:
        return None
    slots = [("name", "name", f["name"])]
    if f.get("area"):
        slots.append(("area", "area", f["area"]))
    if f.get("category") in CATEGORY_PHRASE:
        slots.append(("category_phrase", "category", CATEGORY_PHRASE[f["category"]]))
    if f.get("branches", "").isdigit() and int(f["branches"]) >= 2:
        slots.append(("multi_branch", "branches", "yes"))
    values = {s: v for s, _, v in slots}
    subject, body = render(values)
    pers = [{"slot": s, "field": fld, "value": v, "source_value": f[fld], "source": prov[fld]} for s, fld, v in slots]
    return {"lead_id": lead["lead_id"], "channel": "email", "recipient": f["email"], "subject": subject,
            "body": body, "template_id": TEMPLATE_ID, "personalization": pers,
            "meta": {"score": scored["score"], "max": scored["max"], "evidence": scored["evidence"],
                     "sources": lead["sources"], "conflicts": len(lead["conflicts"])}}
