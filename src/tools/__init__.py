"""Typed tool + MCP execution substrate (Cycle 3): out-of-process workers, a typed
registry, and an MCP tools/list descriptor adapter (no transport). Cycle 5: audited egress
proxy and the cap.browser.fetch builtin."""

from .builtins import BROWSER_FETCH_SCHEMA, BUILTIN_ROOT, browser_fetch_spec, register_browser_fetch
from .egress import EgressProxy
from .mcp_adapter import ToolDescriptor, parse_tools_list
from .registry import ToolRegistry, ToolSpec, TrustState
from .registry_store import RegistryStore
from .sandbox import Sandbox, SandboxUnavailable
from .subprocess_handler import SubprocessHandler, WorkerError, WorkerSpec

__all__ = ["ToolDescriptor", "parse_tools_list", "ToolRegistry", "ToolSpec", "TrustState",
           "SubprocessHandler", "WorkerError", "WorkerSpec",
           "RegistryStore", "Sandbox", "SandboxUnavailable", "EgressProxy",
           "BROWSER_FETCH_SCHEMA", "BUILTIN_ROOT", "browser_fetch_spec", "register_browser_fetch"]
