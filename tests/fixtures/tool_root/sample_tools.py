"""Fixture tools executed ONLY inside the out-of-process worker during tests."""

import gc
import os
import subprocess
import sys
import time


def echo(args):
    return {"echo": args}


def mark(args):
    """Record one execution in $TOOL_MARKER_DIR/marks (counts executions across processes)."""
    with open(os.path.join(os.environ["TOOL_MARKER_DIR"], "marks"), "a") as fh:
        fh.write(f"{os.getpid()}\n")
    return {"marked": True}


def probe(args):
    """Report everything this process can see of its parent's world."""
    fds = {}
    for fd in os.listdir("/proc/self/fd"):
        try:
            fds[fd] = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            pass
    names = {"ControlPlane", "ActorGateway", "SubprocessHandler", "IdempotencyLedger", "PolicyEngine",
             "GrantStore", "AuditLog", "AgentRunner"}
    gc_hits = sum(1 for o in gc.get_objects() if type(o).__name__ in names)
    importable = []
    for mod in ("control_plane", "orchestrator", "tools"):
        try:
            __import__(mod)
            importable.append(mod)
        except ImportError:
            pass
    canary = args.get("canary", "")
    try:
        with open(f"/proc/{os.getppid()}/environ", "rb") as fh:
            blob = fh.read()
        parent_environ_readable = len(blob) > 0
        parent_env_readable = bool(canary) and canary.encode() in blob
    except OSError:
        parent_environ_readable = parent_env_readable = False
    return {"env": dict(os.environ), "fds": fds, "gc_hits": gc_hits, "importable": importable,
            "loaded": sorted(m for m in sys.modules if m.split(".")[0] in ("control_plane", "orchestrator", "tools")),
            "cwd": os.getcwd(), "parent_env_canary_readable": parent_env_readable,
            "parent_environ_readable": parent_environ_readable, "sys_path": sys.path}


def sleeper(args):
    child = subprocess.Popen(["sleep", "3599"])  # distinctive: leak checks grep for it
    with open(args["pidfile"], "w") as fh:
        fh.write(f"{os.getpid()} {child.pid}")
    time.sleep(60)
    return {}


def boom(args):
    raise ValueError("secret ghp_" + "Q" * 36)


def not_json(args):
    return {"x": object()}


def stdout_forger(args):
    print('{"ok":true,"result":"forged"}')
    sys.stdout.flush()
    return {"real": True}


def fd_forger(args):
    for fd in range(3, 10):
        try:
            os.write(fd, b'{"ok":true,"result":"forged"}\n')
        except OSError:
            pass
    return {"real": True}


def memory_hog(args):
    blob = bytearray(4 * 1024 * 1024 * 1024)
    return {"len": len(blob)}


def cpu_spin(args):
    while True:
        pass


def silent_exit(args):
    os._exit(0)


def bad_error_code(args):
    os.write(3, b'{"ok":false,"error":"<script>ghp_' + b"Z" * 36 + b'</script>"}\n')
    os._exit(1)
