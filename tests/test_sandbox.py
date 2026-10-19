"""Sandbox detection fails closed: no bwrap, an untrusted bwrap, or a failing probe means no
tool runs at all (no unsandboxed fallback)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import worker
from tools import Sandbox, SandboxUnavailable, SubprocessHandler
from tools import sandbox as sb


class SandboxDetectionTests(unittest.TestCase):
    def setUp(self):
        sb._detect_cached.cache_clear()
        self.addCleanup(sb._detect_cached.cache_clear)

    def test_available_and_namespaces_requested(self):
        box = Sandbox.detect()
        argv = box.argv("/x/worker.py", "/x", {"PATH": "/usr/bin"})
        for flag in ("--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc", "--die-with-parent",
                     "--new-session", "--clearenv", "--remount-ro"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")
        self.assertNotIn("--bind", argv)  # nothing host-side is mounted writable
        self.assertEqual(argv[-4:], [box.python, "-I", "-S", "/worker/worker.py"])

    def test_missing_bwrap_fails_closed(self):
        with mock.patch.object(sb, "BWRAP_CANDIDATES", ("/nonexistent/bwrap",)):
            with self.assertRaises(SandboxUnavailable) as cm:
                Sandbox.detect()
            self.assertEqual(str(cm.exception), "bwrap_not_found_or_untrusted")
            with self.assertRaises(SandboxUnavailable):
                SubprocessHandler(worker("echo"))  # no handler, hence no unsandboxed run

    def test_untrusted_bwrap_copy_refused(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "bwrap")
            shutil.copy(Sandbox.detect().bwrap, fake)  # same binary, but owned by us
            sb._detect_cached.cache_clear()
            os.chmod(fake, 0o777)
            with mock.patch.object(sb, "BWRAP_CANDIDATES", (fake,)):
                with self.assertRaises(SandboxUnavailable):
                    Sandbox.detect()

    def test_probe_failure_fails_closed(self):
        real = sb.subprocess.run
        with mock.patch.object(sb.subprocess, "run",
                               side_effect=lambda argv, **kw: real(["false"], **kw)):
            with self.assertRaises(SandboxUnavailable) as cm:
                Sandbox.detect()
        self.assertTrue(str(cm.exception).startswith("probe_failed:rc="))

    def test_handler_reports_isolation(self):
        self.assertEqual(SubprocessHandler(worker("echo")).isolation, "bwrap")


if __name__ == "__main__":
    unittest.main()
