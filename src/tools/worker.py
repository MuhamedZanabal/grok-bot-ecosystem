"""Out-of-process tool worker. Run ONLY as a script by tools.subprocess_handler:

    python -I -S worker.py      (isolated mode: ignores PYTHON* env, user site, cwd on path)

Protocol: one JSON object on stdin
    {"v":1, "tool_root": abs dir, "module": "dotted.name", "function": "name",
     "args": {...}, "limits": {"cpu_s": int, "memory_mb": int}}
and exactly one JSON object on stdout
    {"ok": true, "result": <json>}  |  {"ok": false, "error": "<stable code>"}
The tool's own prints go to stderr, so they cannot forge a protocol reply. This file
imports nothing from control_plane/orchestrator; the worker has no handle on them.
"""

import json
import os
import re
import sys

_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
_FUNC = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _reply(fd: int, obj: dict) -> None:
    data = (json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    while data:
        data = data[os.write(fd, data):]


def _limits(cpu_s: int, memory_mb: int) -> None:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        mem = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass  # non-Linux: limits UNKNOWN; the parent's wall-clock kill still applies


def main() -> int:
    out_fd = os.dup(1)          # protocol channel
    os.dup2(2, 1)               # tool stdout -> stderr
    sys.stdout = sys.stderr
    try:
        req = json.loads(sys.stdin.buffer.read(1024 * 1024 + 1) or b"null")
    except ValueError:
        _reply(out_fd, {"ok": False, "error": "worker_bad_request"})
        return 2
    if (not isinstance(req, dict) or req.get("v") != 1 or not isinstance(req.get("args"), dict)
            or not isinstance(req.get("module"), str) or not _MODULE.fullmatch(req["module"])
            or not isinstance(req.get("function"), str) or not _FUNC.fullmatch(req["function"])
            or not isinstance(req.get("tool_root"), str) or not os.path.isabs(req["tool_root"])):
        _reply(out_fd, {"ok": False, "error": "worker_bad_request"})
        return 2
    lim = req.get("limits") or {}
    _limits(int(lim.get("cpu_s", 5)), int(lim.get("memory_mb", 512)))
    sys.path[:] = [req["tool_root"]] + [p for p in sys.path if p.startswith(sys.base_prefix)]
    try:
        module = __import__(req["module"], fromlist=[req["function"]])
        root = os.path.realpath(req["tool_root"]) + os.sep
        if not os.path.realpath(getattr(module, "__file__", None) or "/").startswith(root):
            raise ImportError("module outside tool_root")
        fn = getattr(module, req["function"])
        if not callable(fn):
            raise TypeError("not callable")
    except Exception:
        _reply(out_fd, {"ok": False, "error": "worker_tool_not_found"})
        return 3
    try:
        result = fn(req["args"])
    except MemoryError:
        _reply(out_fd, {"ok": False, "error": "handler_error:MemoryError"})
        return 4
    except Exception as exc:
        _reply(out_fd, {"ok": False, "error": f"handler_error:{type(exc).__name__}"})
        return 4
    try:
        _reply(out_fd, {"ok": True, "result": result})
    except (TypeError, ValueError):
        _reply(out_fd, {"ok": False, "error": "handler_result_not_json"})
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
