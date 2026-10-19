import json
import os
import sys
import tempfile
import time
import unittest

from tests.helpers import OPEN_SCHEMA, TOOL_ROOT, Harness, worker
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
        self.assertEqual(set(r["env"]), {"PATH", "LANG"})
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

    def test_residual_same_uid_proc_environ(self):
        """Residual gap S-0005: the worker runs as the same OS user. Report, do not hide."""
        r = self._probe()
        print(f"\nRESIDUAL parent_proc_environ_readable={r['parent_environ_readable']} "
              f"setenv_canary_visible={r['parent_env_canary_readable']}", file=sys.stderr)
        self.assertIsInstance(r["parent_environ_readable"], bool)

    def test_tool_module_never_loaded_in_parent(self):
        self._probe()
        self.assertNotIn("sample_tools", sys.modules)


class WorkerFailureTests(unittest.TestCase):
    def call(self, fn, args=None, **kw):
        return SubprocessHandler(worker(fn, **kw))(args or {})

    def test_echo_roundtrip(self):
        self.assertEqual(self.call("echo", {"a": [1, "b"]}), {"echo": {"a": [1, "b"]}})

    def test_timeout_kills_worker_and_its_children(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "pids")
            t0 = time.monotonic()
            with self.assertRaises(HandlerTimeout):
                self.call("sleeper", {"pidfile": pidfile}, timeout_s=1.5)
            self.assertLess(time.monotonic() - t0, 10)
            with open(pidfile) as fh:
                worker_pid, child_pid = map(int, fh.read().split())
            deadline = time.monotonic() + 3
            while (alive(worker_pid) or alive(child_pid)) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(alive(worker_pid), "worker survived timeout")
            self.assertFalse(alive(child_pid), "worker's child survived timeout")

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
        self.assertTrue(cm.exception.code.startswith("worker_crashed:rc=-"), cm.exception.code)

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
        with tempfile.TemporaryDirectory() as d:
            req = self.h.req("cap.tool.sleep", args={"pidfile": os.path.join(d, "p")})
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
    def test_non_dumpable_host_hides_environ_from_worker(self):
        """Run a separate host process (so the test runner is not modified) holding a secret in
        its *startup* environment; after set_non_dumpable() the worker cannot read it."""
        import subprocess
        src = os.path.join(os.path.dirname(__file__), "..", "src")
        script = (
            "import json,sys; sys.path[:0]=[%r,%r];"
            "from tools.hardening import set_non_dumpable; from tests.helpers import worker;"
            "from tools import SubprocessHandler;"
            "h=sys.argv[1]=='1' and set_non_dumpable();"
            "r=SubprocessHandler(worker('probe'))({'canary':%r});"
            "print(json.dumps({'hardened':bool(h),'readable':r['parent_environ_readable'],"
            "'canary':r['parent_env_canary_readable']}))"
        ) % (os.path.abspath(src), os.path.abspath(os.path.join(src, "..")), CANARY_VALUE)
        env = dict(os.environ, HOST_SECRET=CANARY_VALUE)
        results = {}
        for flag in ("0", "1"):
            out = subprocess.run([sys.executable, "-c", script, flag], env=env, capture_output=True,
                                 text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            results[flag] = json.loads(out.stdout.strip().splitlines()[-1])
        print(f"\nHARDENING unhardened={results['0']} hardened={results['1']}", file=sys.stderr)
        if not results["1"]["hardened"]:
            self.skipTest("prctl(PR_SET_DUMPABLE) unsupported here (UNKNOWN)")
        self.assertTrue(results["0"]["canary"], "baseline: same-UID worker reads host startup env")
        self.assertFalse(results["1"]["readable"])
        self.assertFalse(results["1"]["canary"])
