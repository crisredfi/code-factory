"""The watchdog: is an agent really stuck? Decision logic with a fake clock, plus two real processes.

Run: python3 -m unittest discover -s CodeFactory/tests -v
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from cf import watchdog  # noqa: E402

HEAL = {"poll_s": 0.2, "confirm_s": 60, "stuck_after_s": 900, "local_stuck_after_s": 1800, "max_run_s": 5400}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Judge(unittest.TestCase):
    """Watch.check() with CPU and file activity scripted by the test."""

    def make(self, heal=HEAL, local=False, local_up=None):
        self.clock = Clock()
        self.cpu = [0.0]
        self.fp = ["same"]
        patches = [
            mock.patch.object(watchdog, "tree_cpu", side_effect=lambda pid: self.cpu[0]),
            mock.patch.object(watchdog, "fingerprint", side_effect=lambda wt, jd: self.fp[0]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return watchdog.Watch(1, None, Path("/nowhere"), heal, local, local_up, clock=self.clock)

    def test_cpu_growth_counts_as_progress(self):
        w = self.make()
        self.clock.now += 1000
        self.cpu[0] = 5.0
        self.assertEqual(w.check()[0], "ACTIVE")

    def test_a_file_change_counts_as_progress(self):
        w = self.make()
        self.clock.now += 1000
        self.fp[0] = "changed"
        self.assertEqual(w.check()[0], "ACTIVE")

    def test_silence_is_only_suspect_until_it_lasts_confirm_s(self):
        w = self.make()
        self.clock.now += HEAL["stuck_after_s"] + 1
        self.assertEqual(w.check()[0], "SUSPECT")
        self.clock.now += 30
        self.assertEqual(w.check()[0], "SUSPECT")
        self.clock.now += HEAL["confirm_s"]
        verdict, reason = w.check()
        self.assertEqual(verdict, "STUCK")
        self.assertIn("no CPU", reason)

    def test_activity_during_suspicion_clears_it(self):
        w = self.make()
        self.clock.now += HEAL["stuck_after_s"] + 1
        self.assertEqual(w.check()[0], "SUSPECT")
        self.cpu[0] = 3.0
        self.clock.now += 5
        self.assertEqual(w.check()[0], "ACTIVE")
        self.assertIsNone(w.suspect_since)

    def test_a_run_over_max_run_s_is_stuck_whatever_it_does(self):
        w = self.make()
        self.clock.now += HEAL["max_run_s"] + 1
        self.cpu[0] = 999.0
        verdict, reason = w.check()
        self.assertEqual(verdict, "STUCK")
        self.assertIn("max_run_s", reason)

    def test_local_model_gets_the_longer_limit(self):
        w = self.make(local=True, local_up=lambda: True)
        self.clock.now += HEAL["stuck_after_s"] + 1
        self.assertEqual(w.check()[0], "ACTIVE")

    def test_local_model_whose_server_is_down_is_stuck_at_its_limit(self):
        w = self.make(local=True, local_up=lambda: False)
        self.clock.now += HEAL["local_stuck_after_s"] + 1
        verdict, reason = w.check()
        self.assertEqual(verdict, "STUCK")
        self.assertIn("not answering", reason)


class ParseCpuTime(unittest.TestCase):
    def test_ps_formats(self):
        self.assertAlmostEqual(watchdog.parse_cputime("0:01.50"), 1.5)
        self.assertAlmostEqual(watchdog.parse_cputime("1:02:03"), 3723.0)
        self.assertAlmostEqual(watchdog.parse_cputime("2-01:00:00"), 2 * 86400 + 3600.0)
        self.assertEqual(watchdog.parse_cputime("garbage"), 0.0)


class RealProcesses(unittest.TestCase):
    def setUp(self):
        self.job = Path(tempfile.mkdtemp())
        self.log = self.job / "run.log"
        self.heal = {"poll_s": 0.2, "confirm_s": 0.4, "stuck_after_s": 1, "local_stuck_after_s": 1, "max_run_s": 30}

    def test_a_busy_process_is_not_killed(self):
        code = "import time\nt = time.time()\nwhile time.time() - t < 3:\n    pass\nprint('finished')\n"
        self.heal["stuck_after_s"] = 5
        rc, out = watchdog.run_watched([sys.executable, "-c", code], "", None, dict(os.environ), self.log,
                                       self.heal, worktree=None, job_dir=self.job)
        self.assertEqual(rc, 0)
        self.assertIn("finished", out)

    def test_a_sleeping_process_is_killed_and_its_children_with_it(self):
        code = ("import subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                "print(child.pid, flush=True)\n"
                "time.sleep(120)\n")
        start = time.monotonic()
        with self.assertRaises(watchdog.Stuck) as ctx:
            watchdog.run_watched([sys.executable, "-c", code], "", None, dict(os.environ), self.log,
                                 self.heal, worktree=None, job_dir=self.job)
        self.assertLess(time.monotonic() - start, 20, "the watchdog should decide within seconds here")
        self.assertIn("no progress", str(ctx.exception))
        self.assertIn("killed by watchdog", self.log.read_text())
        child = int(self.log.read_text().split("--- stdout ---\n", 1)[1].split()[0])
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError, msg="the child process should be gone too"):
            os.kill(child, 0)

    def test_nonzero_exit_is_returned_not_treated_as_stuck(self):
        rc, _ = watchdog.run_watched([sys.executable, "-c", "import sys; sys.exit(3)"], "", None,
                                     dict(os.environ), self.log, self.heal, worktree=None, job_dir=self.job)
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
