import json
import os
import sys
import tempfile
import time
import unittest

from tests.helpers import OPEN_SCHEMA, TOOL_ROOT, Harness, wait_gone, worker
from control_plane import Capability, HandlerTimeout, RiskLevel, Status
from tools import SubprocessHandler, WorkerError, WorkerSpec

CANARY_NAME = "CYCLE3_CANARY_SECRET_TOKEN"
CANARY_VALUE = "canary-" + "9" * 24


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"  # zombie counts as dead
    except FileNotFoundError:
        return False


class WorkerIsolationTests(unittest.TestCase):
    """Tool code runs in a separate process that sees none of the parent's secrets or handles."""

    @classmethod
    def setUpClass(cls):
        os.environ[CANARY_NAME] = CANARY_VALUE

    @classmethod
    def tearDownClass(cls):
        os.environ.pop(CANARY_NAME, None)

    def setUp(self):
        self.h = Harness(inprocess=False)
        self.h.policy.register(Capability("cap.tool.probe", RiskLevel.READ, "", OPEN_SCHEMA))
        self.h.cp.bind("cap.tool.probe", SubprocessHandler(worker("probe")))
        self.h.policy.grant("agent.alpha", "cap.tool.probe")

    def tearDown(self):
        self.h.close()

    def _probe(self):
        out = self.h.cp.execute(self.h.req("cap.tool.probe", args={"canary": CANARY_VALUE}))
        self.assertEqual(out.status, Status.EXECUTED, out)
        return out.result

    def test_env_is_allowlist_only_and_canary_absent(self):
        r = self._probe()
        self.assertEqual(set(r["env"]) - {"PWD"}, {"PATH", "LANG"})
        self.assertEqual(r["env"].get("PWD", "/tmp"), "/tmp")  # set by bwrap --chdir
        self.assertNotIn(CANARY_VALUE, json.dumps(r["env"]))

    def test_no_parent_file_descriptors_inherited(self):
        r = self._probe()
        targets = " ".join(r["fds"].values())
        for path in (self.h.ledger_path, self.h.audit_path, self.h.grants_path):
            self.assertNotIn(path, targets)
        self.assertLessEqual(len(r["fds"]), 6, r["fds"])  # stdio, protocol dup, listdir handle

    def test_tool_code_cannot_import_or_find_control_plane(self):
        r = self._probe()
        self.assertEqual(r["importable"], [])
        self.assertEqual(r["loaded"], [])
        self.assertEqual(r["gc_hits"], 0)
        self.assertNotIn(os.path.join(os.path.dirname(TOOL_ROOT), "..", "..", "src"), r["sys_path"])
        self.assertTrue(r["cwd"].startswith(tempfile.gettempdir()))

    def test_gap_closed_parent_proc_invisible(self):
        """Was test_residual_same_uid_proc_environ (S-0005). Closed in cycle-004: the worker has
        its own PID namespace and /proc, so no host process (and no host environ) is visible."""
        r = self._probe()
        self.assertFalse(r["parent_env_canary_readable"])
        out = self.h.cp.execute(self.h.req("cap.tool.probe", op="op-probe-0002", args={}))
        self.assertNotIn(str(os.getpid()), " ".join(out.result["fds"].values()))

    def test_tool_module_never_loaded_in_parent(self):
        self._probe()
        self.assertNotIn("sample_tools", sys.modules)


class WorkerFailureTests(unittest.TestCase):
    def call(self, fn, args=None, **kw):
        return SubprocessHandler(worker(fn, **kw))(args or {})

    def test_echo_roundtrip(self):
        self.assertEqual(self.call("echo", {"a": [1, "b"]}), {"echo": {"a": [1, "b"]}})

    def test_timeout_kills_worker_and_its_children(self):
        t0 = time.monotonic()
        with self.assertRaises(HandlerTimeout):
            self.call("sleeper", {}, timeout_s=1.5)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(wait_gone(("sleep", "3599")), [], "worker's child survived timeout")

    def test_failure_codes_are_stable_and_secret_free(self):
        cases = {"boom": "handler_error:ValueError", "not_json": "handler_result_not_json",
                 "silent_exit": "worker_crashed:rc=0", "bad_error_code": "worker_error"}
        for fn, code in cases.items():
            with self.subTest(fn=fn):
                with self.assertRaises(WorkerError) as cm:
                    self.call(fn)
                self.assertEqual(cm.exception.code, code)

    def test_tool_stdout_and_fd_forgery_cannot_replace_reply(self):
        self.assertEqual(self.call("stdout_forger"), {"real": True})
        self.assertEqual(self.call("fd_forger"), {"real": True})

    def test_memory_limit(self):
        with self.assertRaises(WorkerError) as cm:
            self.call("memory_hog", memory_mb=256)
        self.assertEqual(cm.exception.code, "handler_error:MemoryError")

    def test_cpu_limit_kills_spinner(self):
        with self.assertRaises(WorkerError) as cm:
            self.call("cpu_spin", cpu_s=1, timeout_s=20)
        # killed by RLIMIT_CPU; bwrap reports 128+signal, a bare process reports -signal
        self.assertRegex(cm.exception.code, r"^worker_crashed:rc=(-\d+|1[3-9]\d)$")

    def test_module_outside_tool_root_refused(self):
        with self.assertRaises(WorkerError) as cm:
            SubprocessHandler(WorkerSpec(TOOL_ROOT, "json", "dumps"))({})
        self.assertEqual(cm.exception.code, "worker_tool_not_found")

    def test_spec_rejects_credential_env_and_bad_values(self):
        for name in ("GITHUB_TOKEN", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "SESSION_ID"):
            with self.assertRaises(ValueError):
                worker("echo", env_allow=(name,))
        for bad in (dict(timeout_s=0), dict(memory_mb=1), dict(env_allow=("lower",))):
            with self.assertRaises(ValueError):
                worker("echo", **bad)
        with self.assertRaises(ValueError):
            WorkerSpec("relative/path", "sample_tools", "echo")
        with self.assertRaises(ValueError):
            WorkerSpec(TOOL_ROOT, "sample_tools", "a.b")

    def test_allowlisted_env_is_passed(self):
        os.environ["TOOL_MARKER_DIR"] = "/tmp/x"
        try:
            env = SubprocessHandler(worker("probe", env_allow=("TOOL_MARKER_DIR",)))({})["env"]
        finally:
            del os.environ["TOOL_MARKER_DIR"]
        self.assertEqual(env.get("TOOL_MARKER_DIR"), "/tmp/x")


class ControlPlaneWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(inprocess=False, timeout=10)
        for cid, fn, kw in (("cap.tool.sleep", "sleeper", {"timeout_s": 1.0}), ("cap.tool.boom", "boom", {})):
            self.h.policy.register(Capability(cid, RiskLevel.READ, "", OPEN_SCHEMA))
            self.h.cp.bind(cid, SubprocessHandler(worker(fn, **kw)))
            self.h.policy.grant("agent.alpha", cid)

    def tearDown(self):
        self.h.close()

    def test_worker_timeout_is_timed_out_state_and_terminal(self):
        req = self.h.req("cap.tool.sleep", args={})
        first = self.h.cp.execute(req)
        second = self.h.cp.execute(req)
        self.assertEqual((first.status, first.reason), (Status.TIMED_OUT, "handler_timeout:worker_killed"))
        self.assertEqual(second.status, Status.TIMED_OUT)

    def test_worker_failure_is_failed_and_secret_not_logged(self):
        out = self.h.cp.execute(self.h.req("cap.tool.boom"))
        self.assertEqual((out.status, out.reason), (Status.FAILED, "worker:handler_error:ValueError"))
        with open(self.h.audit_path) as fh:
            self.assertNotIn("Q" * 36, fh.read())

    def test_bind_is_audited_as_isolated(self):
        with open(self.h.audit_path) as fh:
            binds = [json.loads(line)["data"] for line in fh if '"handler_bound"' in line]
        self.assertTrue(binds and all(b["isolated"] is True for b in binds))


if __name__ == "__main__":
    unittest.main()


class HostHardeningTests(unittest.TestCase):
    def _host(self, code: str) -> dict:
        import subprocess
        src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
        script = ("import json,sys; sys.path[:0]=[%r,%r];" % (src, os.path.dirname(src))) + code
        out = subprocess.run([sys.executable, "-c", script], env=dict(os.environ, HOST_SECRET=CANARY_VALUE),
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_gap_closed_sandbox_hides_host_environ_even_if_unhardened(self):
        """Was the Cycle 3 baseline 'same-UID worker reads host startup env' (canary True).
        cycle-004: a host that is NOT hardened still leaks nothing, because the sandboxed worker
        cannot see host processes at all."""
        r = self._host("from tests.helpers import worker; from tools import SubprocessHandler;"
                       "from control_plane import is_non_dumpable;"
                       "r=SubprocessHandler(worker('scan_proc_environ'))({'canary':%r});"
                       "print(json.dumps(dict(r, host_hardened=is_non_dumpable())))" % CANARY_VALUE)
        print(f"\nSANDBOX_PROC unhardened_host={r}", file=sys.stderr)
        self.assertFalse(r["host_hardened"])
        self.assertFalse(r["ok"], "worker found host secret in some /proc/*/environ")
        self.assertLessEqual(r["visible_pids"], 3)

    def test_control_plane_construction_enforces_non_dumpable(self):
        r = self._host("from control_plane import is_non_dumpable; b=is_non_dumpable();"
                       "from tests.helpers import Harness; h=Harness(inprocess=False);"
                       "print(json.dumps({'before': b, 'after': is_non_dumpable()})); h.close()")
        print(f"\nHARDENING control_plane_construction={r}", file=sys.stderr)
        self.assertEqual(r, {"before": False, "after": True})

    def test_control_plane_refuses_to_start_unhardened(self):
        from unittest import mock
        from control_plane import HostHardeningError
        with mock.patch("control_plane.executor.set_non_dumpable", return_value=False):
            with self.assertRaises(HostHardeningError):
                Harness(inprocess=False)
