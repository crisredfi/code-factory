"""The examples in CodeFactory/examples must pass the controller's own parsers and gates.

If a gate changes its format, these tests fail until the examples are updated, so the examples
never show a format the factory no longer accepts.
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CF_ROOT = HERE.parent
sys.path.insert(0, str(CF_ROOT / "scripts"))
from cf import paths, config, decide, reviewers, util  # noqa: E402

EXAMPLES = CF_ROOT / "examples"


class Examples(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = paths.JOBS
        paths.JOBS = self.tmp / "jobs"
        paths.JOBS.mkdir()
        # Copy each example under its real job id (issue-N), as the controller would name it.
        self.jobs = {}
        for folder in sorted((EXAMPLES / "jobs").iterdir()):
            st = json.loads((folder / "state.json").read_text())
            dst = paths.JOBS / st["job_id"]
            shutil.copytree(folder, dst)
            self.jobs[folder.name] = st["job_id"]
        self.cfg = config.load()

    def tearDown(self):
        paths.JOBS = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, job_id, rel):
        return (paths.JOBS / job_id / rel).read_text()

    def test_every_example_state_file_is_a_known_state(self):
        from cf.model import COLUMNS
        for job_id in self.jobs.values():
            st = json.loads(self.read(job_id, "state.json"))
            self.assertIn(st["state"], COLUMNS, job_id)
            self.assertTrue(st["history"], job_id)

    def test_001_intake_header_and_reviewer_adjustments_parse(self):
        doc = self.read("issue-12", "intake.md")
        self.assertEqual(util.first_word(util.field(doc, "DECISION")), "ACCEPT")
        self.assertEqual(util.first_word(util.field(doc, "RISK")), "LOW")
        self.assertEqual(util.names_in(util.field(doc, "REVIEWERS_DROP")), ["architecture"])
        self.assertEqual(util.names_in(util.field(doc, "REVIEWERS_ADD")), [])

    def test_001_spec_has_criteria_the_gate_accepts(self):
        st = json.loads(self.read("issue-12", "state.json"))
        st["state"] = "SPEC"
        st["worktree"] = None
        decide.decide_spec(st, self.cfg)  # raises if no '- [ ]' criterion
        self.assertEqual(len(re.findall(r"^\s*-\s\[\s\]\s+\S", self.read("issue-12", "spec.md"), re.M)), 3)

    def test_001_single_plan_validates(self):
        plan = decide.validate_plan("issue-12", self.cfg)
        self.assertEqual(plan["mode"], "single")
        self.assertEqual(len(plan["subtasks"]), 1)

    def test_001_checks_header_is_read_as_gate_pass(self):
        self.assertEqual(util.field(self.read("issue-12", "round-1/checks.md"), "GATE"), "PASS")

    def test_001_reviews_lead_to_merge_pending_with_the_real_decider(self):
        st = json.loads(self.read("issue-12", "state.json"))
        st["state"], st["round"], st["round_limit"], st["risk"] = "REVIEW", 1, 3, "LOW"
        st["reviewers_override"] = None
        self.assertEqual(reviewers.review_names(st, self.cfg), ["code", "security"])
        to, note = decide.decide_review(st, self.cfg)
        self.assertEqual(to, "MERGE_PENDING", note)

    def test_002_split_validates_and_waits_for_approval(self):
        issues = decide.validate_split("issue-20")
        self.assertEqual([i["id"] for i in issues], ["c1", "c2", "c3", "c4"])
        plan = decide.validate_plan("issue-20", self.cfg)
        self.assertEqual(plan["mode"], "split")
        self.assertEqual(util.first_word(util.field(self.read("issue-20", "build.md"), "STATUS")), "SPLIT")

    def test_003_resumed_job_keeps_the_old_decision_and_the_new_one(self):
        old = self.read("issue-31", "intake-1.md")
        new = self.read("issue-31", "intake.md")
        self.assertEqual(util.first_word(util.field(old, "DECISION")), "NEEDS-INFO")
        self.assertEqual(util.first_word(util.field(new, "DECISION")), "ACCEPT")
        self.assertIn("1. Export to which format", old.split("## Questions", 1)[1])

    def test_003_issue_quotes_the_answer_and_marks_the_factory_comment(self):
        text = self.read("issue-31", "issue.md")
        self.assertIn("the factory", text)
        self.assertIn("Markdown only, and no trash", text)
        self.assertIn("quoted data", text)

    def test_comment_examples_start_with_the_marker_and_have_a_heading(self):
        blocks = re.findall(r"```\n(.*?)```", (EXAMPLES / "comments.md").read_text(), re.S)
        self.assertGreaterEqual(len(blocks), 5)
        for b in blocks:
            self.assertTrue(b.startswith("<!-- factory -->\n### Factory: "), b[:60])
            self.assertLessEqual(len(b), 3500)


if __name__ == "__main__":
    unittest.main()
