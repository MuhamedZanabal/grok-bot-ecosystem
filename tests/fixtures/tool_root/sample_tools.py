"""Fixture tools executed ONLY inside the out-of-process worker during tests."""

import gc
import os
import subprocess
import sys
import time


def echo(args):
    return {"echo": args}


def mark(args):
    """Side-effect stand-in; executions are counted host-side by the tests."""
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
    subprocess.Popen(["sleep", "3599"])  # distinctive: host-side leak checks grep for it
    time.sleep(60)
    return {}


def setsid_escape(args):
    """Try to outlive the timeout: a child in its own session, plus a double-forked daemon."""
    subprocess.Popen(["sleep", "3598"], start_new_session=True)
    if os.fork() == 0:
        os.setsid()
        if os.fork() == 0:
            os.execv("/usr/bin/sleep", ["sleep", "3597"])
        os._exit(0)
    time.sleep(60)
    return {}


def detach_and_return(args):
    """Return normally but leave a setsid'd child behind (must not survive the worker)."""
    subprocess.Popen(["sleep", "3596"], start_new_session=True)
    return {"left_child": True}


def read_path(args):
    try:
        with open(args["path"], "rb") as fh:
            return {"ok": True, "len": len(fh.read(4096))}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}


def list_path(args):
    try:
        return {"ok": True, "entries": sorted(os.listdir(args["path"]))[:50]}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}


def write_path(args):
    try:
        with open(args["path"], "w") as fh:
            fh.write("pwned")
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}


def tcp_connect(args):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect((args["host"], args["port"]))
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}
    finally:
        s.close()


def unix_connect(args):
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(args["path"])
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}
    finally:
        s.close()


def udp_dns(args):
    import socket
    try:
        socket.getaddrinfo("example.com", 443)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}


def scan_proc_environ(args):
    """Search every visible process's environ for the canary."""
    hits, pids = 0, 0
    for d in os.listdir("/proc"):
        if d.isdigit():
            pids += 1
            try:
                with open(f"/proc/{d}/environ", "rb") as fh:
                    hits += args["canary"].encode() in fh.read()
            except OSError:
                pass
    return {"ok": hits > 0, "visible_pids": pids}


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
