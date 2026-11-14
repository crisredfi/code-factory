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


class BuilderInstructions(Base):
    """The local model follows these literally: the local-state command and 'do not push' must be in every committing phase."""

    def test_committing_phases_say_not_to_push_and_how_to_drop_local_state(self):
        from cf import config  # noqa: F401
        st = {"job_id": "issue-9", "state": "BUILD", "round": 2, "phase": "revise", "issue": 9, "branch": "b",
              "worktree": None, "round_limit": 5}
        for phase in ["integrate", "revise"]:
            with self.subTest(phase=phase):
                st["phase"] = phase
                extra = agents.stage_items("build", self.cfg, st, phase)[0]["extra"]
                self.assertRegex(extra, r"[Dd]o NOT push")
                self.assertRegex(extra, r"[Nn]ever run `git rm`")
                self.assertNotRegex(extra, r"Commit, push|commit, push|run exactly this command")


class DropLocalState(Base):
    """Real git: the factory takes .claude/ out of a branch with one commit, and leaves real work alone."""

    def git(self, *args, cwd):
        import subprocess
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"]}
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env, check=True).stdout

    def setUp(self):
        super().setUp()
        from cf import gate
        self.gate = gate
        origin, work = self.tmp / "origin.git", self.tmp / "wt"
        self.git("init", "-q", "--bare", "-b", "main", str(origin), cwd=self.tmp)
        self.git("clone", "-q", str(origin), str(work), cwd=self.tmp)
        (work / "app.txt").write_text("base\n")
        self.git("add", "-A", cwd=work); self.git("commit", "-q", "-m", "base", cwd=work)
        self.git("push", "-q", "-u", "origin", "main", cwd=work)
        self.git("checkout", "-q", "-b", "factory/issue-9", cwd=work)
        (work / ".claude").mkdir(); (work / ".claude" / "ce-memory.db").write_text("db")
        (work / "app.txt").write_text("base\nfeature\n")
        self.git("add", "-A", cwd=work); self.git("commit", "-q", "-m", "feature with tool state", cwd=work)
        self.wt = work

    def test_removes_local_state_in_one_commit_and_keeps_files_on_disk(self):
        removed = self.gate.drop_local_state(self.wt, "main", self.cfg["local_state_paths"])
        self.assertEqual(removed, [".claude/ce-memory.db"])
        self.assertTrue((self.wt / ".claude" / "ce-memory.db").exists())
        changed = self.git("diff", "--name-only", "origin/main...HEAD", cwd=self.wt).split()
        self.assertEqual(changed, ["app.txt"])
        self.assertEqual(self.gate.drop_local_state(self.wt, "main", self.cfg["local_state_paths"]), [])

    def test_refuses_when_something_else_is_staged(self):
        (self.wt / "app.txt").write_text("other\n")
        self.git("add", "app.txt", cwd=self.wt)
        with self.assertRaises(FactoryError):
            self.gate.drop_local_state(self.wt, "main", self.cfg["local_state_paths"])


class CodingSplit(Base):
    """Orchestrator (build model) plans and commits; coders write code on the coding model, by difficulty."""

    def test_coding_model_follows_difficulty(self):
        cfg = {"coding": {"default": "local:qwen3.6-35b-a3b-splash", "hard": "local:qwen3.8-27b-splash"}}
        self.assertEqual(agents.coding_model({"difficulty": "NORMAL"}, cfg, "x"), "local:qwen3.6-35b-a3b-splash")
        self.assertEqual(agents.coding_model({"difficulty": "HARD"}, cfg, "x"), "local:qwen3.8-27b-splash")
        self.assertEqual(agents.coding_model({}, cfg, "x"), "local:qwen3.6-35b-a3b-splash")
        self.assertEqual(agents.coding_model({"difficulty": "HARD"}, {}, "fallback"), "fallback")

    def test_subtasks_and_revise_coder_use_the_coding_model_not_the_orchestrator(self):
        cfg = dict(self.cfg, coding={"default": "local:a", "hard": "local:b"})
        st = {"difficulty": "HARD", "round": 2}
        plan = {"subtasks": [{"id": "s1", "specialist": "backend-engineer", "covers": []}]}
        self.assertEqual(agents.subtask_items(plan, plan["subtasks"], cfg, st)[0]["model"], "local:b")
        self.assertEqual(agents.coder_items(st, cfg)[0]["model"], "local:b")
        self.assertEqual(agents.coder_items(st, cfg)[0]["agent"], "factory-coder")

    def test_a_failed_orchestrator_run_does_not_rerun_the_coder(self):
        paths.PROFILE = self.tmp / "queue" / "run-profile.json"
        state.save_state(state.new_state("issue-9", 9, "t", "", ["factory"], self.cfg, 1))
        state.job_dir("issue-9").mkdir(parents=True, exist_ok=True)
        st = state.load_state("issue-9")
        st.update(state="BUILD", phase="revise", round=2, done=["BUILD|revise|2|revise-code", "BUILD|revise|2|build-revise"])
        state.save_state(st)
        steps._forget_stage("issue-9")
        self.assertEqual(state.load_state("issue-9")["done"], ["BUILD|revise|2|revise-code"])


class BlockedRouting(Base):
    def test_session_refusals_need_a_human_and_build_problems_go_to_the_coder(self):
        from cf import decide
        self.assertTrue(decide.is_environment_block("every git command returned \"This command requires approval\""))
        self.assertTrue(decide.is_environment_block("swift test was refused: non-interactive session"))
        self.assertFalse(decide.is_environment_block("AC-7: the migration test does not compile"))
        self.assertFalse(decide.is_environment_block("AppTests/Foo.swift line 12 unterminated raw string"))


class HoldRelease(Base):
    def test_only_queued_jobs_can_be_held_and_release_returns_them(self):
        from cf import lifecycle, model
        self.assertIn("HELD", model.COLUMNS)
        self.assertEqual(model.TRANSITIONS["QUEUED"] & {"HELD"}, {"HELD"})
        self.assertEqual(model.TRANSITIONS["HELD"], {"QUEUED"})
        st = {"job_id": "issue-1", "state": "BUILD"}
        with self.assertRaises(Exception):
            lifecycle.hold_job(st, True)


class DirtyTreeIgnoresLocalState(Base):
    def test_untracked_local_state_is_not_uncommitted_work(self):
        from cf import gate
        pats = [".claude/", "project.xcworkspace/"]
        self.assertTrue(gate.is_local_state(".claude/", pats) or gate.is_local_state("x/.claude/", pats))
        self.assertTrue(gate.is_local_state("DooMail.xcodeproj/project.xcworkspace/", pats))
        self.assertFalse(gate.is_local_state("AppTests/Foo.swift", pats))


if __name__ == "__main__":
    unittest.main()
