"""Cross-check MAC-chained stores against the audit log's anchors (cycle-008, D-0022).

The audit log is verified first (hash chain). Then, for each store:
  * every anchored (seq, mac) must exist in the store with the identical mac
    -> catches deletion (incl. tail truncation, which a store chain alone cannot see)
       and edits/rewrites by someone holding the store key;
  * every store row must be anchored -> catches forged inserts;
  * the store's own chain must verify.
Anchors: memory_write{seq, mac} and claim_recorded{seq, mac} (created rows only).
"""

import json

from control_plane.audit import verify_chain

ANCHOR_EVENTS = {"memory": "memory_write", "claims": "claim_recorded"}


def _anchors(audit_path: str) -> dict[str, dict[int, str]]:
    out = {name: {} for name in ANCHOR_EVENTS}
    by_event = {v: k for k, v in ANCHOR_EVENTS.items()}
    with open(audit_path) as fh:
        for line in fh:
            rec = json.loads(line)
            name = by_event.get(rec.get("event"))
            data = rec.get("data") or {}
            if name and isinstance(data.get("seq"), int) and isinstance(data.get("mac"), str):
                out[name][data["seq"]] = data["mac"]
    return out


def crosscheck(audit_path: str, stores: dict) -> dict:
    """stores: {"memory": MemoryStore, "claims": ClaimLedger} (either may be omitted)."""
    audit_ok, n, why = verify_chain(audit_path)
    report = {"ok": audit_ok, "audit_chain_ok": audit_ok, "audit_records": n, "audit_reason": why, "stores": {}}
    anchors = _anchors(audit_path) if audit_ok else {k: {} for k in ANCHOR_EVENTS}
    for name, store in sorted(stores.items()):
        rows = dict(store.anchor_rows())
        anchored = anchors[name]
        missing = sorted(s for s, mac in anchored.items() if rows.get(s) != mac)
        unanchored = sorted(s for s in rows if s not in anchored)
        chain_ok, _, chain_reason = store.verify_chain()
        ok = chain_ok and not missing and not unanchored and audit_ok
        report["stores"][name] = {"ok": ok, "rows": len(rows), "anchors": len(anchored), "chain_ok": chain_ok,
                                  "chain_reason": chain_reason, "missing_or_altered": missing[:20],
                                  "unanchored": unanchored[:20]}
        report["ok"] = report["ok"] and ok
    return report


def main(argv=None) -> int:
    """python -m integrity --audit A [--memory M] [--claims C]; keys from files named by
    --memory-key-file / --claims-key-file (never on the command line). Exit 0 iff ok."""
    import argparse
    import sys
    p = argparse.ArgumentParser(prog="integrity")
    p.add_argument("--audit", required=True)
    for name in ANCHOR_EVENTS:
        p.add_argument(f"--{name}")
        p.add_argument(f"--{name}-key-file")
    a = p.parse_args(argv)
    stores = {}
    if a.memory:
        from memory import MemoryStore
        stores["memory"] = MemoryStore(a.memory, open(a.memory_key_file, "rb").read())
    if a.claims:
        from research import ClaimLedger
        stores["claims"] = ClaimLedger(a.claims, open(a.claims_key_file, "rb").read())
    rep = crosscheck(a.audit, stores)
    print(json.dumps(rep, sort_keys=True), file=sys.stdout)
    return 0 if rep["ok"] else 1
