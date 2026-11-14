"""A retry resumes at the agents that did not succeed. Agents are stubbed; nothing runs claude or a shell."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_factory import Base  # noqa: E402

from cf import agents, api, paths, state, steps  # noqa: E402
from cf.util import FactoryError  # noqa: E402


class Resume(Base):
    def setUp(self):
        super().setUp()
        self.saved_profile = paths.PROFILE
        paths.PROFILE = self.tmp / "queue" / "run-profile.json"  # never read the real profile
        self.cfg["max_parallel_subtasks"] = 1
        self.cfg["self_heal"]["enabled"] = False
        st = state.new_state("issue-9", 9, "t", "", ["factory"], self.cfg, 1)
        st.update(state="REVIEW", round=1, reviewers=["security", "code"], risk="LOW")
        state.save_state(st)
        state.job_dir("issue-9").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        paths.PROFILE = self.saved_profile
        super().tearDown()

    def items(self):
        return [{"agent": f"reviewer-{n}", "model": "claude-haiku-5-5", "tag": f"review-{n}", "stage": f"review.{n}",
                 "output": f"round-1/review-{n}.md", "extra": ""} for n in ["security", "code"]]

    def test_failed_reviewer_is_retried_alone_and_the_passed_one_is_kept(self):
        calls = []
        fail = {"review-security": True}

        def fake(job_id, it, spec, prompt, cwd, log, cfg, dry, out):
            calls.append(it["tag"])
            if fail.get(it["tag"]):
                raise FactoryError(f"{it['tag']} exited 1")
            return "ok"

        with mock.patch.object(agents, "_run_stage_once", side_effect=fake):
            with self.assertRaises(FactoryError):
                agents.execute("issue-9", self.items(), self.cfg, False, out=self.quiet)
            self.assertEqual(sorted(calls), ["review-code", "review-security"])
            self.assertIn("REVIEW|None|1|review-code", state.load_state("issue-9")["done"])

            calls.clear()
            fail["review-security"] = False
            agents.execute("issue-9", self.items(), self.cfg, False, out=self.quiet)
            self.assertEqual(calls, ["review-security"], "only the failed reviewer runs again")

    def test_a_new_round_is_a_new_set_of_runs(self):
        st = state.load_state("issue-9")
        st["done"] = ["REVIEW|None|1|review-code"]
        st["round"] = 2
        state.save_state(st)
        self.assertEqual(agents.stage_key(state.load_state("issue-9"), "review-code"), "REVIEW|None|2|review-code")

    def test_invalid_output_forgets_only_that_stage(self):
        st = state.load_state("issue-9")
        st["done"] = ["REVIEW|None|1|review-code", "BUILD|plan|1|build-plan"]
        state.save_state(st)
        steps._forget_stage("issue-9")
        self.assertEqual(state.load_state("issue-9")["done"], ["BUILD|plan|1|build-plan"])


class RoundRows(Base):
    def test_a_failed_round_stays_red_after_a_later_round_passes(self):
        st = {"state": "REVIEW", "round": 2, "reviewers": ["code"]}
        att = {"Gate": [{"round": 1, "verdict": "FAIL"}, {"round": 2, "verdict": "PASS"}],
               "Review": [{"round": 2, "name": "code", "verdict": "PASS"}]}
        rows = api._rounds(st, att)
        self.assertEqual(rows[0], {"round": 1, "build": "done", "gate": "FAIL", "reviews": {"code": "skipped"}})
        self.assertEqual(rows[1], {"round": 2, "build": "done", "gate": "PASS", "reviews": {"code": "PASS"}})

    def test_the_current_round_shows_what_is_running(self):
        rows = api._rounds({"state": "BUILD", "round": 2, "reviewers": []}, {"Gate": [{"round": 1, "verdict": "FAIL"}], "Review": []})
        self.assertEqual(rows[1]["build"], "running")
        self.assertEqual(rows[1]["gate"], "pending")


if __name__ == "__main__":
    unittest.main()
