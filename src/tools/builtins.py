"""Repository-owned tools that run in the sandbox (tool root: src/builtin_tools)."""

import os

from control_plane import RiskLevel

from .subprocess_handler import WorkerSpec

BUILTIN_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "builtin_tools")

BROWSER_FETCH_SCHEMA = {
    "type": "object", "required": ["url"], "additionalProperties": False,
    "properties": {
        "url": {"type": "string", "maxLength": 2048, "pattern": "https?://[^\\s]+"},
        "max_bytes": {"type": "integer", "minimum": 1, "maximum": 2 * 1024 * 1024},
        "max_redirects": {"type": "integer", "minimum": 0, "maximum": 5},
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 30},
        "max_links": {"type": "integer", "minimum": 0, "maximum": 200},
        "max_text": {"type": "integer", "minimum": 0, "maximum": 100000},
    },
}
BROWSER_FETCH_RISK = RiskLevel.EXTERNAL  # any request leaves the box (D-0015)


def browser_fetch_spec(egress: tuple[str, ...], **kw) -> WorkerSpec:
    """WorkerSpec for cap.browser.fetch, allowed to reach exactly `egress` (host:port entries)."""
    kw.setdefault("timeout_s", 30.0)
    return WorkerSpec(BUILTIN_ROOT, "browser", "fetch", egress=tuple(egress), **kw)


def register_browser_fetch(registry, egress: tuple[str, ...], *, by: str, capability_id: str = "cap.browser.fetch",
                           **kw):
    return registry.register_local(capability_id, BROWSER_FETCH_RISK,
                                   "Fetch a web page (no JS) via the audited egress proxy; returns untrusted page data",
                                   BROWSER_FETCH_SCHEMA, browser_fetch_spec(egress, **kw), by=by,
                                   reason="builtin browser tier (D-0015)")
