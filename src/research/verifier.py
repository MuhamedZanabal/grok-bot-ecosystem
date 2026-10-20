"""Claim verifier (cycle-007, D-0020).

admit()  : run before a claim is stored. Any hard failure REFUSES the claim.
verdict(): run on every read. Returns "verified" or "UNKNOWN" with reasons (stale,
           contradicted, source_rejected, evidence_unverifiable:<why>). Never "true".
Evidence is re-checked against the MAC-chained memory store every time.
"""

import re

RESERVED = frozenset({"trusted", "trust", "execution_allowed", "status", "source_id", "accepted"})
MAX_QUOTE = 300


def _token(word: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(word) + r"(?![A-Za-z0-9_.-])", text) is not None


class Verifier:
    def __init__(self, memory, sources, ledger, clock, max_age_s: float = 30 * 86400) -> None:
        self.memory, self.sources, self.ledger, self.clock, self.max_age_s = memory, sources, ledger, clock, max_age_s

    def pointer(self, claim: dict, ptr: dict) -> tuple[str | None, list[str]]:
        """(hard_failure_or_None, soft_reasons) for one evidence pointer."""
        if not isinstance(ptr, dict):
            return "bad_pointer", []
        src = self.sources.get(ptr.get("source_id", ""))
        if src is None:
            return "unknown_source", []
        row, why = self.memory.get(ptr.get("record_id", ""))
        if row is None:
            if why == "expired":
                return None, ["stale"]
            return ("record_not_found" if why == "not_found" else f"evidence_tampered:{why}"), []
        if row["content_sha256"] != ptr.get("content_sha256"):
            return "hash_mismatch", []
        if row["origin_url"] != src["url"]:
            return "origin_mismatch", []
        quote = ptr.get("quote")
        if not isinstance(quote, str) or not quote or len(quote) > MAX_QUOTE or quote not in row["content"]:
            return "quote_not_in_record", []
        if not (_token(claim["attribute"], quote) and _token(claim["value"], quote)):
            return "quote_does_not_support_claim", []
        if not _token(claim["subject"], row["content"]):
            return "subject_not_in_record", []
        soft = []
        if self.clock() - row["created_at"] > self.max_age_s:
            soft.append("stale")
        if src["status"] == "rejected":
            soft.append("source_rejected")
        return None, soft

    def admit(self, claim: dict) -> str | None:
        """None if the claim may be stored, else a stable refusal reason."""
        if claim.get("attribute") in RESERVED:
            return "reserved_attribute"
        evidence = claim.get("evidence") or []
        if not evidence:
            return "claim_without_evidence"
        for ptr in evidence:
            hard, _ = self.pointer(claim, ptr)
            if hard:
                return hard
        return None

    def _intact(self, claim: dict) -> tuple[bool, list[str]]:
        reasons = []
        if not claim.get("evidence"):
            return False, ["claim_without_evidence"]
        for ptr in claim["evidence"]:
            hard, soft = self.pointer(claim, ptr)
            if hard:
                return False, [f"evidence_unverifiable:{hard}"]
            reasons += soft
        return True, sorted(set(reasons))

    def verdict(self, claim: dict) -> tuple[str, list[str]]:
        ok, reasons = self._intact(claim)
        if not ok or reasons:
            return "UNKNOWN", reasons
        for peer in self.ledger.peers(claim):
            if peer["value"] != claim["value"] and self._intact(peer) == (True, []):
                return "UNKNOWN", ["contradicted"]
        return "verified", []
