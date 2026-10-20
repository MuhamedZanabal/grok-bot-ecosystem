"""Schedules (cycle-008, D-0022): integer-second slots, UTC, no DST semantics.

  {"kind": "interval", "every_s": N, "anchor": T}   slots T + k*N
  {"kind": "cron", "expr": "m h dom mon dow"}      minute slots; *, a-b, a,b, */n, a-b/n
due(s, after, upto, limit) -> (most recent <= limit slots in (after, upto] ascending, total count)
"""

import time

MAX_CRON_SCAN_S = 366 * 86400  # a larger gap is counted as UNKNOWN (-1), latest slot still found
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


class ScheduleError(ValueError):
    pass


def _field(text: str, lo: int, hi: int) -> frozenset | None:
    if text == "*":
        return None
    out = set()
    for part in text.split(","):
        rng, _, step = part.partition("/")
        if not (step == "" or step.isdigit()) or (step and int(step) < 1):
            raise ScheduleError("bad_step")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            x, _, y = rng.partition("-")
            if not (x.isdigit() and y.isdigit()):
                raise ScheduleError("bad_range")
            a, b = int(x), int(y)
        elif rng.isdigit():
            a = b = int(rng)
        else:
            raise ScheduleError("bad_field")
        if not (lo <= a <= b <= hi):
            raise ScheduleError("out_of_range")
        out.update(range(a, b + 1, int(step) if step else 1))
    return frozenset(out)


def parse(s: dict) -> dict:
    if not isinstance(s, dict):
        raise ScheduleError("bad_schedule")
    if s.get("kind") == "interval" and set(s) <= {"kind", "every_s", "anchor"}:
        every, anchor = s.get("every_s"), s.get("anchor", 0)
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (every, anchor)) or every < 1:
            raise ScheduleError("bad_interval")
        return {"kind": "interval", "every_s": every, "anchor": anchor}
    if s.get("kind") == "cron" and set(s) == {"kind", "expr"} and isinstance(s["expr"], str):
        parts = s["expr"].split()
        if len(parts) != 5:
            raise ScheduleError("cron_needs_5_fields")
        return {"kind": "cron", "expr": s["expr"], "fields": [_field(p, *r) for p, r in zip(parts, _RANGES)]}
    raise ScheduleError("bad_schedule")


def _cron_match(fields, t: int) -> bool:
    g = time.gmtime(t)
    minute, hour, dom, mon, dow = fields
    vals = (g.tm_min, g.tm_hour, g.tm_mday, g.tm_mon, (g.tm_wday + 1) % 7)
    if any(f is not None and v not in f for f, v in ((minute, vals[0]), (hour, vals[1]), (mon, vals[3]))):
        return False
    if dom is not None and dow is not None:  # classic cron: either day field may match
        return vals[2] in dom or vals[4] in dow
    return (dom is None or vals[2] in dom) and (dow is None or vals[4] in dow)


def due(s: dict, after: int, upto: int, limit: int) -> tuple[list[int], int]:
    if upto <= after:
        return [], 0
    if s["kind"] == "interval":
        e, a = s["every_s"], s["anchor"]
        hi, lo = (upto - a) // e, (after - a) // e  # slot indices k with after < a+k*e <= upto
        total = max(0, hi - lo)
        return [a + k * e for k in range(max(lo + 1, hi - limit + 1), hi + 1)], total
    start = max(after, upto - MAX_CRON_SCAN_S)
    slots, total = [], 0
    t = (start // 60 + 1) * 60
    while t <= upto:
        if _cron_match(s["fields"], t):
            total += 1
            slots.append(t)
            if len(slots) > limit:
                slots.pop(0)
        t += 60
    return slots, (total if start == after else -1)
