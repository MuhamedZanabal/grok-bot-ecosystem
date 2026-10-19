import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.helpers import Harness
from control_plane import KillSwitch, LoopState, Status

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


class KillSwitchTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        for cap in ("cap.fs.read", "cap.fs.write", "cap.email.send", "cap.payment.transfer"):
            self.h.policy.grant("agent.alpha", cap)

    def tearDown(self):
        self.h.close()

    def test_kill_switch_blocks_every_risk_level(self):
        self.h.kill.engage("test")
        for cap in ("cap.fs.read", "cap.fs.write", "cap.email.send", "cap.payment.transfer"):
            tok = self.h.approve(cap, op=f"op-{cap}")
            out = self.h.cp.execute(self.h.req(cap, op=f"op-{cap}", token=tok))
            self.assertEqual(out.status, Status.KILLED, cap)
        self.assertEqual(self.h.total_calls(), 0)

    def test_kill_switch_blocks_replays_too(self):
        self.h.cp.execute(self.h.req("cap.fs.read"))
        self.h.kill.engage()
        self.assertEqual(self.h.cp.execute(self.h.req("cap.fs.read")).status, Status.KILLED)

    def test_file_kill_switch_from_another_process(self):
        env = dict(os.environ, PYTHONPATH=SRC)
        subprocess.run([sys.executable, "-m", "control_plane", "kill", "engage", self.h.kill_path],
                       check=True, env=env, capture_output=True)
        fresh = KillSwitch(self.h.kill_path)  # no in-memory state
        self.assertTrue(fresh.engaged())
        self.h.kill._engaged = False
        self.assertEqual(self.h.cp.execute(self.h.req("cap.fs.read")).status, Status.KILLED)
        subprocess.run([sys.executable, "-m", "control_plane", "kill", "release", self.h.kill_path],
                       check=True, env=env, capture_output=True)
        self.assertEqual(self.h.cp.execute(self.h.req("cap.fs.read")).status, Status.EXECUTED)

    def test_governor_observes_kill(self):
        self.h.kill.engage()
        self.assertEqual(self.h.governor.admit(), LoopState.KILLED)

    def test_fails_closed_when_state_unknowable(self):
        self.assertTrue(KillSwitch("\0bad-path").engaged())
        with mock.patch("control_plane.killswitch.os.stat", side_effect=PermissionError):
            self.assertTrue(KillSwitch(self.h.kill_path).engaged())
            self.assertEqual(self.h.cp.execute(self.h.req("cap.fs.read")).status, Status.KILLED)


if __name__ == "__main__":
    unittest.main()
