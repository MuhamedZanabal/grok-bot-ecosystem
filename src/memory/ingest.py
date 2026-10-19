"""Wire cap.browser.fetch results into memory as untrusted records, through the control
plane (policy + idempotency + audit), never by writing the store directly."""

import hashlib

from .handlers import WRITE_CAP


def fetch_to_memory_args(fetch_result: dict, fetch_operation_id: str) -> dict | None:
    if not isinstance(fetch_result, dict) or fetch_result.get("ok") is not True:
        return None
    parts = [fetch_result.get("title") or "", fetch_result.get("text") or ""]
    links = fetch_result.get("links") or []
    if links:
        parts.append("links: " + " ".join(links))
    content = "\n\n".join(p for p in parts if p)
    if not content:
        return None
    return {"content": content, "origin_url": fetch_result["url"], "source_id": "cap.browser.fetch",
            "source_ref": fetch_operation_id[:200]}


def ingest_fetch(control_plane, actor: str, fetch_operation_id: str, fetch_result: dict):
    """Returns the ControlPlane Outcome of the memory write, or None if nothing to store.
    The operation id is derived from the fetch operation id, so re-ingesting is a replay."""
    args = fetch_to_memory_args(fetch_result, fetch_operation_id)
    if args is None:
        return None
    op = "ingest:" + hashlib.sha256(fetch_operation_id.encode()).hexdigest()[:32]
    return control_plane.execute({"operation_id": op, "actor": actor, "capability": WRITE_CAP, "args": args})
