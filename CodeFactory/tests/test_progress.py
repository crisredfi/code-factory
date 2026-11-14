"""Per-stage progress on the dashboard cards, built from the job's state and files.

Run: python3 -m unittest discover -s CodeFactory/tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from cf import api, state  # noqa: E402


class Progress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jd = Path(self.tmp.name) / "issue-9"
        self.jd.mkdir()
        self._saved = state.job_dir
        state.job_dir = lambda job_id: self.jd

    def tearDown(self):
        state.job_dir = self._saved
        self.tmp.cleanup()

    def job(self, s, gate=None, rnd=1, history=None):
        return {"job_id": "issue-9", "state": s, "round": rnd, "gate": gate,
                "history": history or [{"note": "moved", "to": s}]}

    def statuses(self, st):
        return {x["name"]: x["status"] for x in api.progress(st)["steps"]}

    def test_blocked_build_fails_the_build_step_and_shows_the_reason(self):
        (self.jd / "build.md").write_text("STATUS: BLOCKED\n\n## Blocked\n- swift test needs approval\n")
        out = api.progress(self.job("NEEDS_HUMAN"))
        self.assertEqual(self.statuses(self.job("NEEDS_HUMAN")), {
            "Intake": "done", "Spec": "done", "Build": "failed", "Gate": "pending",
            "Review": "pending", "Merge": "pending"})
        self.assertIn("swift test needs approval", out["reason"])

    def test_failed_gate_is_red_and_its_summary_is_the_reason(self):
        rd = self.jd / "round-1"
        rd.mkdir()
        (rd / "checks.md").write_text("GATE: FAIL\n\n## Summary\n- checks exited 2\n\n## Output (tail)\n")
        (self.jd / "build.md").write_text("STATUS: READY\n")
        st = self.job("NEEDS_HUMAN", gate="FAIL", rnd=1)
        self.assertEqual(self.statuses(st)["Gate"], "failed")
        self.assertIn("checks exited 2", api.progress(st)["reason"])

    def test_passed_gate_and_review_show_green(self):
        st = self.job("MERGE_PENDING", gate="PASS")
        s = self.statuses(st)
        self.assertEqual(s["Gate"], "done")
        self.assertEqual(s["Merge"], "waiting")

    def test_completed_job_is_green_all_the_way(self):
        s = self.statuses(self.job("COMPLETE", gate="PASS"))
        self.assertTrue(all(v == "done" for v in s.values()), s)

    def test_waiting_for_the_author_is_amber_not_red(self):
        s = self.statuses(self.job("NEEDS_INFO"))
        self.assertEqual(s["Intake"], "waiting")

    def test_a_block_verdict_in_review_is_red(self):
        rd = self.jd / "round-1"
        rd.mkdir()
        (rd / "review-security.md").write_text("VERDICT: BLOCK\nRISK: HIGH\n")
        self.assertEqual(self.statuses(self.job("REVIEW", gate="PASS"))["Review"], "failed")


if __name__ == "__main__":
    unittest.main()
