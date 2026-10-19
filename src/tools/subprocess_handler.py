"""IsolatedHandler that runs one tool call in a fresh, OS-sandboxed worker process.

Per call: a bubblewrap sandbox (tools.sandbox: no network, read-only minimal filesystem,
private /tmp, own PID namespace) running `python -I -S /worker/worker.py`, in a new
session/process group, with close_fds, an env built ONLY from an explicit allowlist,
rlimits (CPU, address space, no core dumps) set inside the worker, and a wall-clock
deadline after which the sandbox's process group is SIGKILLed (the PID namespace then
takes every descendant down) and reaped before HandlerTimeout is raised.
Construction fails closed with SandboxUnavailable if the sandbox cannot be established.
"""

from dataclasses import dataclass
import json
import os
import re
import signal
import subprocess
import tempfile

from control_plane import HandlerFailure, HandlerTimeout, IsolatedHandler

from .sandbox import SANDBOX_TOOL_ROOT, Sandbox

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py")
BASE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
SENSITIVE_ENV = re.compile(r"(TOKEN|SECRET|PASSW|KEY|CREDENTIAL|COOKIE|SESSION|AUTH)", re.IGNORECASE)
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


ERROR_CODE = re.compile(r"[A-Za-z0-9_:=.-]{1,100}")


class WorkerError(HandlerFailure):
    """Worker failed in a non-timeout way. Codes from the worker are untrusted text and are
    reduced to a safe charset/length before use."""


@dataclass(frozen=True)
class WorkerSpec:
    tool_root: str
    module: str
    function: str
    timeout_s: float = 10.0
    cpu_s: int = 10
    memory_mb: int = 512
    env_allow: tuple[str, ...] = ()
    max_output_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if not os.path.isabs(self.tool_root) or not os.path.isdir(self.tool_root):
            raise ValueError("tool_root must be an existing absolute directory")
        if not MODULE.fullmatch(self.module) or not MODULE.fullmatch(self.function) or "." in self.function:
            raise ValueError("bad module/function name")
        if not (0 < self.timeout_s <= 300) or not (1 <= self.cpu_s <= 300) or not (32 <= self.memory_mb <= 8192):
            raise ValueError("limits out of range")
        for name in self.env_allow:
            if not ENV_NAME.fullmatch(name):
                raise ValueError(f"bad env name {name!r}")
            if SENSITIVE_ENV.search(name):
                raise ValueError(f"env {name!r} looks like a credential; secrets are not passed to tools")


class SubprocessHandler(IsolatedHandler):
    __slots__ = ("spec", "_sandbox")
    isolation = "bwrap"

    def __init__(self, spec: WorkerSpec) -> None:
        if not isinstance(spec, WorkerSpec):
            raise TypeError("spec must be a WorkerSpec")
        object.__setattr__(self, "spec", spec)
        object.__setattr__(self, "_sandbox", Sandbox.detect())  # raises SandboxUnavailable

    def __setattr__(self, name, value) -> None:
        raise AttributeError("SubprocessHandler is immutable")

    def __repr__(self) -> str:
        return f"SubprocessHandler({self.spec.module}.{self.spec.function})"

    def _env(self) -> dict:
        env = dict(BASE_ENV)
        for name in self.spec.env_allow:
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def __call__(self, args: dict):
        s = self.spec
        request = json.dumps({"v": 1, "tool_root": SANDBOX_TOOL_ROOT, "module": s.module, "function": s.function,
                              "args": args, "limits": {"cpu_s": s.cpu_s, "memory_mb": s.memory_mb}},
                             allow_nan=False).encode()
        with tempfile.TemporaryDirectory(prefix="tool-") as cwd:
            argv = self._sandbox.argv(WORKER, s.tool_root, self._env())
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, cwd=cwd, env={"PATH": "/usr/bin:/bin"},
                                    close_fds=True, start_new_session=True)
            try:
                out, _ = proc.communicate(request, timeout=s.timeout_s)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                raise HandlerTimeout("worker_killed") from None
            finally:
                if proc.poll() is None:  # any other exit path: never leave a live worker
                    _kill_group(proc)
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # reap stragglers the tool spawned in its group
            except (ProcessLookupError, PermissionError):
                pass
        if len(out) > s.max_output_bytes:
            raise WorkerError("worker_output_too_large")
        try:
            reply = json.loads(out.decode("utf-8").strip().splitlines()[-1]) if out.strip() else None
        except (ValueError, UnicodeDecodeError):
            raise WorkerError("worker_protocol_error") from None
        if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
            raise WorkerError(f"worker_crashed:rc={proc.returncode}" if reply is None else "worker_protocol_error")
        if not reply["ok"]:
            code = reply.get("error")
            raise WorkerError(code if isinstance(code, str) and ERROR_CODE.fullmatch(code) else "worker_error")
        return reply.get("result")


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover
        pass
    for pipe in (proc.stdin, proc.stdout):
        if pipe is not None and not pipe.closed:
            pipe.close()
