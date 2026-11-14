"""The flow opens the PR after the gate and reviewers pass, merges when auto-merge allows it, and sends a
failed merge to a human. Gate warnings are shown, never blocking.

Run: python3 -m unittest discover -s CodeFactory/tests -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from cf import decide, gate, github, lifecycle, paths, state, steps, util  # noqa: E402
from cf.util import FactoryError  # noqa: E402
from test_factory import Base  # noqa: E402


class WarningsAreAdvisory(unittest.TestCase):
    def test_warn_line_is_read(self):
        self.assertEqual(gate.warnings_in("WARN: 1200 changed lines (limit 1000): consider splitting the PR\n"),
                         ["1200 changed lines (limit 1000): consider splitting the PR"])

    def test_verify_summary_warning_is_read(self):
        out = "verify: diff-scope: ok, warning: 1200 changed lines is over the advisory limit\nverify: total: PASS\n"
        self.assertEqual(gate.warnings_in(out), ["1200 changed lines is over the advisory limit"])

    def test_clean_output_has_no_warnings(self):
        self.assertEqual(gate.warnings_in("verify: diff-scope: ok (300 lines)\nverify: total: PASS\n"), [])


class PublishAndMerge(Base):
    def job_in_review(self, auto_enabled: bool):
        self.cfg["auto_merge"] = {"enabled": auto_enabled, "max_risk": "MEDIUM"}
        util.write_json(paths.CONFIG, self.cfg)
        st = state.new_state("issue-9", 9, "Add thing", "https://x/9", ["factory"], self.cfg_now(), 1)
        st.update(state="REVIEW", branch="factory/issue-9", worktree=str(self.tmp), round=1, round_limit=3)
        state.save_state(st)
        self.write("issue-9", "round-1/checks.md", "GATE: PASS\nCOMMIT: abc\n\n## Summary\n- checks passed\n")
        self.write("issue-9", "round-1/review-code.md", "VERDICT: PASS\nRISK: LOW\n")
        self.write("issue-9", "round-1/review-security.md", "VERDICT: PASS\nRISK: LOW\n")
        self.write("issue-9", "build.md", "STATUS: READY\n\nAdds the thing.\n")
        return st

    def run_review_to_merge(self):
        decide.DECIDERS["REVIEW"] = lambda st, cfg: ("MERGE_PENDING", "all reviews PASS, risk LOW")
        self.addCleanup(lambda: decide.DECIDERS.__setitem__("REVIEW", decide.decide_review))
        return steps.apply("issue-9", self.cfg_now())

    def test_passing_job_gets_a_pr_and_merges_when_auto_merge_is_on(self):
        self.job_in_review(auto_enabled=True)
        with mock.patch.object(lifecycle.util, "git") as git, \
             mock.patch.object(github, "create_pr", return_value="https://x/pull/50") as create, \
             mock.patch.object(lifecycle, "merge_job") as merge:
            to, note = self.run_review_to_merge()
        self.assertEqual(to, "COMPLETE", note)
        self.assertIn("push", git.call_args.args)
        create.assert_called_once()
        self.assertEqual(create.call_args.args[1], "factory/issue-9")
        self.assertTrue(merge.called)
        body = (state.job_dir("issue-9") / "pr-body.md").read_text()
        self.assertIn("Closes #9", body)
        self.assertIn("GATE: PASS", body)
        self.assertIn("🤖 Generated with [Claude Code]", body)

    def test_passing_job_waits_for_a_human_when_auto_merge_is_off(self):
        self.job_in_review(auto_enabled=False)
        with mock.patch.object(lifecycle.util, "git"), \
             mock.patch.object(github, "create_pr", return_value="https://x/pull/51"), \
             mock.patch.object(lifecycle, "merge_job") as merge:
            to, note = self.run_review_to_merge()
        self.assertEqual(to, "MERGE_PENDING")
        self.assertIn("https://x/pull/51", note)
        self.assertFalse(merge.called)
        self.assertEqual(state.load_state("issue-9")["pr_url"], "https://x/pull/51")

    def test_a_failed_merge_goes_to_a_human_with_the_reason(self):
        self.job_in_review(auto_enabled=True)
        with mock.patch.object(lifecycle.util, "git"), \
             mock.patch.object(github, "create_pr", return_value="https://x/pull/52"), \
             mock.patch.object(lifecycle, "merge_job", side_effect=FactoryError("gh pr merge failed: checks")):
            to, note = self.run_review_to_merge()
        self.assertEqual(to, "NEEDS_HUMAN")
        self.assertIn("auto-merge failed", note)
        self.assertEqual(state.load_state("issue-9")["state"], "NEEDS_HUMAN")

    def test_a_push_failure_leaves_the_job_in_review_for_a_retry(self):
        self.job_in_review(auto_enabled=True)
        with mock.patch.object(lifecycle.util, "git", side_effect=FactoryError("push rejected")), \
             mock.patch.object(github, "create_pr") as create:
            with self.assertRaises(FactoryError):
                self.run_review_to_merge()
        self.assertFalse(create.called)
        self.assertEqual(state.load_state("issue-9")["state"], "REVIEW")

    def test_a_pr_the_builder_named_is_kept(self):
        st = self.job_in_review(auto_enabled=False)
        st["pr_url"] = "https://x/pull/7"
        state.save_state(st)
        with mock.patch.object(lifecycle.util, "git"), mock.patch.object(github, "create_pr") as create:
            url = lifecycle.publish_pr(state.load_state("issue-9"), self.cfg_now())
        self.assertEqual(url, "https://x/pull/7")
        self.assertFalse(create.called)


if __name__ == "__main__":
    unittest.main()
