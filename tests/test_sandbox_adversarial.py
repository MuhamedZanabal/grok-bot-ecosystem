"""Adversarial cases against the OS worker sandbox (cycle-004).

Each case is a tool that *tries* to escape: read host state (ledger / grant DB / audit /
credentials / home), write outside its private /tmp, reach the network or host sockets, see
host processes, or outlive its worker via setsid / double-fork. An escape is any case where
the attempt succeeds as observed from the HOST side.

Success metric: escapes == 0, and no sandbox skips (SandboxUnavailable is a failure here).
"""
import os
import socket
import sys
import tempfile
import threading
import unittest

from tests.helpers import TOOL_ROOT, Harness, wait_gone, worker
from control_plane import HandlerTimeout
from tools import Sandbox, SubprocessHandler
from tools.sandbox import mount_ancestors

ALLOWED_ROOT = {"bin", "dev", "lib", "lib32", "lib64", "libx32", "proc", "sbin", "tmp", "tool", "usr", "worker"}


class SandboxAdversarialTests(unittest.TestCase):
    def setUp(self):
        Sandbox.detect()  # raises SandboxUnavailable -> test error, never a skip
        self.h = Harness(inprocess=False)
        self.hostdir = tempfile.TemporaryDirectory()
        self.accepts = 0
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.srv.settimeout(0.2)
        self._stop = False
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()
        self.usock_path = os.path.join(self.hostdir.name, "host.sock")
        self.usock = socket.socket(socket.AF_UNIX)
        self.usock.bind(self.usock_path)
        self.usock.listen(1)

    def _serve(self):
        while not self._stop:
            try:
                c, _ = self.srv.accept()
                self.accepts += 1
                c.close()
            except OSError:
                pass

    def tearDown(self):
        self._stop = True
        self._t.join(2)
        self.srv.close()
        self.usock.close()
        self.hostdir.cleanup()
        self.h.close()

    def run_tool(self, name, args, timeout_s=10.0):
        return SubprocessHandler(worker(name, timeout_s=timeout_s))(args)

    def _cases(self):
        def read(path):
            return lambda: self.run_tool("read_path", {"path": path})["ok"]

        anc = mount_ancestors()

        def lst(path):
            # visible only if it lists something beyond the bare path to a read-only python mount
            def go():
                r = self.run_tool("list_path", {"path": path})
                return r["ok"] and not set(r["entries"]) <= anc.get(path, set())
            return go

        def write(path, host_path=None, inside_counts=False):
            host_path = host_path or path

            def go():
                inside = self.run_tool("write_path", {"path": path})["ok"]
                try:
                    with open(host_path) as fh:
                        landed = "pwned" in fh.read()
                except OSError:
                    landed = False
                return landed or (inside and inside_counts)
            return go

        def timeout_escape():
            try:
                self.run_tool("setsid_escape", {}, timeout_s=1.5)
            except HandlerTimeout:
                pass
            return bool(wait_gone(("sleep", "3598"), ("sleep", "3597")))

        def detach():
            self.run_tool("detach_and_return", {})
            return bool(wait_gone(("sleep", "3596")))

        def root_listing():
            r = self.run_tool("list_path", {"path": "/"})
            return not r["ok"] or not set(r["entries"]) <= ALLOWED_ROOT | anc.get("/", set())

        def proc_view():
            r = self.run_tool("list_path", {"path": "/proc"})
            pids = [e for e in r["entries"] if e.isdigit()]
            return str(os.getpid()) in pids or len(pids) > 3

        port = self.srv.getsockname()[1]
        yield "read ledger db", read(self.h.ledger_path)
        yield "read grant db", read(self.h.grants_path)
        yield "read audit log", read(self.h.audit_path)
        yield "read gh credentials", read(os.path.expanduser("~/.config/gh/hosts.yml"))
        yield "read /etc/passwd", read("/etc/passwd")
        yield "read host /proc/<parent>/environ", read(f"/proc/{os.getpid()}/environ")
        yield "list home", lst(os.path.expanduser("~"))
        yield "list repo checkout", lst(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        worker_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "tools", "worker.py"))
        yield "write tool_root", write("/tool/pwned.py", os.path.join(TOOL_ROOT, "pwned.py"), inside_counts=True)
        yield "write /usr", write("/usr/pwned", inside_counts=True)
        yield "write host temp dir", write(os.path.join(self.hostdir.name, "pwned"))
        yield "overwrite worker.py", write("/worker/worker.py", worker_src, inside_counts=True)
        yield "private /tmp is not host /tmp", write(os.path.join("/tmp", f"pwned-{os.getpid()}"))
        yield "tcp to internet", lambda: self.run_tool("tcp_connect", {"host": "1.1.1.1", "port": 53})["ok"]
        yield "tcp to host loopback listener", lambda: (
            self.run_tool("tcp_connect", {"host": "127.0.0.1", "port": port})["ok"] or self.accepts > 0)
        yield "unix socket to host", lambda: self.run_tool("unix_connect", {"path": self.usock_path})["ok"]
        yield "dns lookup", lambda: self.run_tool("udp_dns", {})["ok"]
        yield "see host processes", proc_view
        yield "minimal root filesystem", root_listing
        yield "setsid/double-fork survives timeout", timeout_escape
        yield "setsid child survives normal return", detach

    def test_zero_escapes(self):
        escapes, n = [], 0
        for name, attempt in self._cases():
            n += 1
            with self.subTest(case=name):
                if attempt():
                    escapes.append(name)
        print(f"\nSANDBOX_ADVERSARIAL cases={n} escapes={len(escapes)}", file=sys.stderr)
        self.assertGreaterEqual(n, 12)
        self.assertEqual(escapes, [])


if __name__ == "__main__":
    unittest.main()
