"""Typed economic numbers (cycle-009, D-0024).

Every number is {"value", "provenance", "unit", "source"}. provenance is measured |
configured_estimate | unknown, and value is None exactly when provenance is unknown.
UNKNOWN is never coerced to 0: a total over any unknown is unknown, and it carries its count.
"""

import math

PROVENANCE = ("measured", "configured_estimate", "unknown")
_KEYS = {"value", "provenance", "unit", "source"}
BOUNDED = {"ratio": 1.0, "risk_level": 3.0}


class QuantityError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def q(value, provenance: str, unit: str, source: str) -> dict:
    return check({"value": value, "provenance": provenance, "unit": unit, "source": source})


def measured(value, unit: str, source: str) -> dict:
    return q(value, "measured", unit, source)


def estimate(value, unit: str, source: str) -> dict:
    return q(value, "configured_estimate", unit, source)


def unknown(unit: str, source: str) -> dict:
    return q(None, "unknown", unit, source)


def check(x, field: str = "quantity") -> dict:
    if not isinstance(x, dict) or set(x) != _KEYS:
        raise QuantityError(f"{field}:bad_shape")
    v, p = x["value"], x["provenance"]
    if p not in PROVENANCE or not isinstance(x["unit"], str) or not isinstance(x["source"], str):
        raise QuantityError(f"{field}:bad_provenance")
    if (p == "unknown") != (v is None):
        raise QuantityError(f"{field}:unknown_iff_null")
    if v is not None:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise QuantityError(f"{field}:bad_value")
        if v > BOUNDED.get(x["unit"], math.inf):
            raise QuantityError(f"{field}:out_of_range")
    return x


def total(items, unit: str) -> dict:
    """Sum of known values plus an explicit unknown count; value is None if any is unknown."""
    known = [i["value"] for i in items if i["provenance"] != "unknown"]
    n_unknown = sum(1 for i in items if i["provenance"] == "unknown")
    prov = "unknown" if n_unknown else ("configured_estimate" if any(
        i["provenance"] == "configured_estimate" for i in items) else "measured")
    return {"value": None if n_unknown else round(sum(known), 9), "known_sum": round(sum(known), 9),
            "known": len(known), "unknown": n_unknown, "provenance": prov, "unit": unit}
