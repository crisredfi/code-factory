"""What the card shows: a failed round stays visible after a retry passes, errors are retried and then escalate,
a prose PR value is never a link, every card says what happens next, and two ticks never overlap.

Run: python3 -m unittest discover -s CodeFactory/tests -v
"""
import fcntl
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from cf import api, paths, queue, state, steps  # noqa: E402
from test_factory import Base  # noqa: E402

FAIL_R2 = "GATE: FAIL\nCOMMIT: abc\n\n## Summary\n- verify: swift-test FAIL\n"
PASS_R3 = "GATE: PASS\nCOMMIT: def\n\n## Summary\n- checks passed\n"


class Visibility(Base):
    def job(self, **fields):
        st = state.new_state("issue-9", 9, "Thing", "https://x/9", ["factory"], self.cfg_now(), 1)
        st.update(fields)
        state.save_state(st)
        return st

    def test_failed_round_stays_red_after_the_retry_passes(self):
        self.job(state="REVIEW", round=3, round_limit=5, gate="PASS")
        self.write("issue-9", "round-2/checks.md", FAIL_R2)
        self.write("issue-9", "round-3/checks.md", PASS_R3)
        p = api.progress(state.load_state("issue-9"))
        gate = next(s for s in p["steps"] if s["name"] == "Gate")
        self.assertEqual(gate["status"], "done")
        failed = [a for a in gate["attempts"] if a["status"] == "failed"]
        self.assertEqual([a["round"] for a in failed], [2], gate["attempts"])
        self.assertIn("swift-test FAIL", failed[0]["detail"])

    def test_gate_failure_says_the_builder_is_retrying(self):
        self.job(state="BUILD", phase="revise", round=3, round_limit=5, gate="FAIL")
        self.write("issue-9", "round-2/checks.md", FAIL_R2)
        p = api.progress(state.load_state("issue-9"))
        gate = next(s for s in p["steps"] if s["name"] == "Gate")
        self.assertEqual(gate["status"], "failed")
        self.assertIn("builder retrying in round 3", gate["detail"])

    def test_next_step_names_the_retry_and_the_round(self):
        st = self.job(state="BUILD", phase="revise", round=3, round_limit=5, gate="FAIL")
        text = api.next_action(st, self.cfg_now(), {"next_at": None})
        self.assertIn("Gate failed in round 2", text)
        self.assertIn("round 3 of 5", text)
        self.assertIn("next tick continues it", text)

    def test_an_error_is_retried_then_sent_to_a_human(self):
        self.job(state="BUILD", phase="revise", round=2, round_limit=5)
        cfg = self.cfg_now()
        cfg["self_heal"]["max_step_errors"] = 3
        lines = []
        for _ in range(2):
            steps._record_error("issue-9", cfg, "build.md needs STATUS", lines.append)
        st = state.load_state("issue-9")
        self.assertEqual(st["state"], "BUILD")
        self.assertEqual(st["error_streak"], 2)
        self.assertEqual(st["last_error"]["message"], "build.md needs STATUS")
        self.assertIn("retry on next tick", st["history"][-1]["note"])
        self.assertIn("Error in build", api.next_action(st, cfg, {"next_at": None}))
        steps._record_error("issue-9", cfg, "build.md needs STATUS", lines.append)
        st = state.load_state("issue-9")
        self.assertEqual(st["state"], "NEEDS_HUMAN")
        self.assertIn("gave up after 3 errors", st["history"][-1]["note"])

    def test_a_clean_run_clears_the_error_streak(self):
        st = self.job(state="BUILD", phase="revise", round=2, round_limit=5, error_streak=2,
                      last_error={"at": "x", "message": "boom"})
        steps._clear_error("issue-9")
        st = state.load_state("issue-9")
        self.assertEqual(st["error_streak"], 0)
        self.assertIsNone(st["last_error"])

    def test_prose_is_not_a_pr_link(self):
        self.assertIsNone(api._web_url("none (requires `gh pr create` — approval needed)"))
        self.assertIsNone(api._web_url(None))
        self.assertEqual(api._web_url("https://github.com/o/r/pull/3"), "https://github.com/o/r/pull/3")

    def test_job_view_drops_a_non_link_pr_value(self):
        st = self.job(state="NEEDS_HUMAN", pr_url="none (requires gh pr create)")
        view = api.job_view(st, self.cfg_now())
        self.assertIsNone(view["pr_url"])

    def test_a_second_tick_waits_for_the_first(self):
        lock = paths.INDEX.parent / "tick.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lines = []
            ran = queue.run_tick(self.cfg_now(), dry=True, out=lines.append)
        self.assertFalse(ran)
        self.assertEqual(lines, ["another tick is running; skipped"])

    def test_done_and_queued_have_plain_sentences(self):
        cfg = self.cfg_now()
        self.assertEqual(api.next_action(self.job(state="QUEUED"), cfg, {}), "Queued: starts when a slot is free.")
        self.assertIn("Merge it when you are ready", api.next_action(
            self.job(state="MERGE_PENDING"), dict(cfg, auto_merge={"enabled": False, "max_risk": "MEDIUM"}), {}))


if __name__ == "__main__":
    unittest.main()
