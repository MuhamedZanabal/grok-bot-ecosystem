"""OS sandbox for tool workers, built on bubblewrap (bwrap).

Each worker runs in fresh user, PID, network, mount, IPC and UTS namespaces (cgroup if the
kernel allows) with:
  * no network: an empty net namespace, so even the host loopback and host Unix sockets are absent;
  * a read-only filesystem containing only /usr (+ merged-usr symlinks), the Python
    installation, the worker script at /worker/worker.py and the tool root at /tool;
  * a private tmpfs at /tmp (the only writable path; cwd);
  * a fresh /proc for its own PID namespace, so host processes are invisible;
  * all capabilities dropped, a new session, and --die-with-parent.
Because the worker lives in its own PID namespace, killing the sandbox (or the worker
exiting) tears down every descendant, including ones that called setsid().

Fail closed: if bwrap is missing, not root-owned, writable by others, or the probe fails,
detect() raises SandboxUnavailable and no tool runs. There is no unsandboxed fallback.
"""

from dataclasses import dataclass
import functools
import os
import stat
import subprocess
import sys

BWRAP_CANDIDATES = ("/usr/bin/bwrap", "/bin/bwrap")
NAMESPACES = ("--unshare-user", "--unshare-ipc", "--unshare-pid", "--unshare-net", "--unshare-uts",
              "--unshare-cgroup-try")
SANDBOX_TOOL_ROOT = "/tool"
SANDBOX_WORKER = "/worker/worker.py"


class SandboxUnavailable(RuntimeError):
    """The OS sandbox cannot be established here; tools must not run."""


def _trusted_binary(path: str) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    return (stat.S_ISREG(st.st_mode) and st.st_uid == 0
            and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and os.access(path, os.X_OK))


def _python_mounts() -> list[str]:
    roots = []
    for p in (sys.base_prefix, os.path.dirname(os.path.dirname(os.path.realpath(sys.executable)))):
        p = os.path.realpath(p)
        if p == "/usr" or p.startswith("/usr/") or p in roots or p == "/":
            continue
        roots.append(p)
    return roots


@dataclass(frozen=True)
class Sandbox:
    bwrap: str
    python: str

    def argv(self, worker_path: str, tool_root: str, env: dict[str, str]) -> list[str]:
        a = [self.bwrap, *NAMESPACES, "--die-with-parent", "--new-session", "--cap-drop", "ALL",
             "--ro-bind", "/usr", "/usr"]
        for p in ("/bin", "/lib", "/lib64", "/sbin", "/lib32", "/libx32"):
            if os.path.islink(p):
                a += ["--symlink", os.readlink(p), p]
            elif os.path.isdir(p):
                a += ["--ro-bind", p, p]
        for p in _python_mounts():
            a += ["--ro-bind", p, p]
        a += ["--ro-bind", worker_path, SANDBOX_WORKER, "--ro-bind", tool_root, SANDBOX_TOOL_ROOT,
              "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--chdir", "/tmp",
              "--remount-ro", "/", "--clearenv"]
        for k, v in sorted(env.items()):
            a += ["--setenv", k, v]
        return a + ["--", self.python, "-I", "-S", SANDBOX_WORKER]

    @staticmethod
    def detect() -> "Sandbox":
        return _detect()


@functools.lru_cache(maxsize=1)
def _detect_cached() -> Sandbox:
    if not sys.platform.startswith("linux"):
        raise SandboxUnavailable("unsupported_platform")
    bwrap = next((p for p in BWRAP_CANDIDATES if _trusted_binary(p)), None)
    if bwrap is None:
        raise SandboxUnavailable("bwrap_not_found_or_untrusted")
    box = Sandbox(bwrap, os.path.realpath(sys.executable))
    probe_dir = os.path.dirname(os.path.abspath(__file__))
    argv = box.argv(os.path.join(probe_dir, "worker.py"), probe_dir, {"PATH": "/usr/bin:/bin"})
    argv = argv[: argv.index("--") + 1] + [box.python, "-I", "-S", "-c", _probe_code(probe_dir)]
    try:
        r = subprocess.run(argv, capture_output=True, timeout=20, env={"PATH": "/usr/bin:/bin"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailable(f"probe_failed:{type(exc).__name__}") from None
    if r.returncode != 0:
        raise SandboxUnavailable(f"probe_failed:rc={r.returncode}:{r.stderr[:200].decode(errors='replace').strip()}")
    return box


def mount_ancestors() -> dict[str, set[str]]:
    """Directory -> the only entries a worker may see in it, for paths above extra mounts."""
    seen: dict[str, set[str]] = {}
    for p in _python_mounts():
        parts = p.strip("/").split("/")
        for i in range(len(parts) - 1):
            seen.setdefault("/" + "/".join(parts[:i]) if i else "/", set()).add(parts[i])
        seen.setdefault("/" + "/".join(parts[:-1]), set()).add(parts[-1])
    return seen


def _probe_code(probe_dir: str) -> str:
    """Fails (non-zero) unless: no network, host checkout and home invisible, and every
    directory above an extra (python) mount shows nothing but the path to that mount."""
    return (
        "import os,socket\n"
        f"anc={ {k: sorted(v) for k, v in mount_ancestors().items() if k != '/'}!r}\n"
        "for d,allowed in anc.items():\n"
        " assert sorted(os.listdir(d))==allowed,(d,os.listdir(d))\n"
        f"assert not os.path.exists({os.path.abspath(probe_dir)!r})\n"
        f"h={os.path.expanduser('~')!r}\n"
        "assert not os.path.isdir(h) or sorted(os.listdir(h))==anc.get(h,[]),h\n"
        "s=socket.socket(); s.settimeout(1)\n"
        "try:\n s.connect(('1.1.1.1',53)); raise SystemExit(9)\n"
        "except OSError: pass\n")


def _detect() -> Sandbox:
    return _detect_cached()  # failures are not cached (lru_cache does not cache exceptions)
