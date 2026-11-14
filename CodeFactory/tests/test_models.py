"""Run profiles, model specs and LM Studio loading. No LM Studio or Claude is needed: `lms` and `claude` are fakes.

Run: python3 -m unittest discover -s CodeFactory/tests -v
"""
import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from cf import agents, cli, models, paths, report, state, steps, util, watchdog  # noqa: E402
from cf.util import FactoryError  # noqa: E402

REAL = Path(__file__).resolve().parent.parent
REAL_CONFIG = (REAL / "factory.config.json").read_text()

FAKE_LMS = r'''#!/usr/bin/env python3
import json, os, sys
st_path, log_path = os.environ["FAKE_LMS_STATE"], os.environ["FAKE_LMS_LOG"]
st = json.load(open(st_path))
args = sys.argv[1:]
with open(log_path, "a") as log:
    log.write(" ".join(args) + "\n")
def save():
    json.dump(st, open(st_path, "w"))
if args[:1] == ["ls"]:
    print(json.dumps([{"type": "llm", "modelKey": k} for k in st["installed"]]))
elif args[:1] == ["ps"]:
    print(json.dumps([{"modelKey": k, "identifier": k} for k in st["loaded"]]))
elif args[:1] == ["load"]:
    if args[1] not in st["installed"]:
        print("no such model", file=sys.stderr); sys.exit(1)
    st["loaded"].append(args[1]); save()
elif args[:1] == ["unload"]:
    st["loaded"].remove(args[1]); save()
'''

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys, time, pathlib
rec = pathlib.Path(os.environ["FAKE_CLAUDE_LOG"])
prior = len(rec.read_text().splitlines()) if rec.exists() else 0
sys.stdin.read()
with rec.open("a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "base": os.environ.get("ANTHROPIC_BASE_URL"),
                        "auth": os.environ.get("ANTHROPIC_AUTH_TOKEN"),
                        "api_key": os.environ.get("ANTHROPIC_API_KEY")}) + "\n")
if os.environ.get("FAKE_CLAUDE_MODE") == "hang" or (os.environ.get("FAKE_CLAUDE_MODE") == "hang_first" and prior == 0):
    time.sleep(60)
print("done")
'''


def _exe(path: Path, text: str) -> str:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class Env(unittest.TestCase):
    """A temporary factory: config, jobs, agents, a fake lms and a fake claude."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = {k: getattr(paths, k) for k in ["CONFIG", "INDEX", "JOBS", "AGENTS", "BOARD", "PROFILE",
                                                       "LMSTUDIO_LOCK"]}
        (self.tmp / "queue").mkdir()
        (self.tmp / "jobs").mkdir()
        shutil.copytree(REAL / "agents", self.tmp / "agents")
        paths.CONFIG = self.tmp / "factory.config.json"
        paths.INDEX = self.tmp / "queue" / "queue.json"
        paths.JOBS = self.tmp / "jobs"
        paths.AGENTS = self.tmp / "agents"
        paths.BOARD = self.tmp / "BOARD.md"
        paths.PROFILE = self.tmp / "queue" / "run-profile.json"
        paths.LMSTUDIO_LOCK = self.tmp / "queue" / "lmstudio.lock"
        (self.tmp / "queue" / "queue.json").write_text('{"jobs": {}, "next_pos": 1}')

        self.cfg = json.loads(REAL_CONFIG)
        self.cfg["target_repo"] = ""
        self.cfg["github"]["comments"] = False
        # Nothing listens on port 9, so the LM Studio health check fails without touching a real server.
        self.cfg["models"]["local"]["base_url"] = "http://127.0.0.1:9"
        self.cfg["self_heal"].update({"poll_s": 0.1, "confirm_s": 0.3, "stuck_after_s": 1,
                                      "local_stuck_after_s": 1, "max_run_s": 20, "max_retries": 1})
        util.write_json(paths.CONFIG, self.cfg)

        self.lms_state = self.tmp / "lms-state.json"
        self.lms_log = self.tmp / "lms.log"
        util.write_json(self.lms_state, {"installed": ["qwen3.6-35b-a3b-splash", "qwen3.8-27b-splash"], "loaded": []})
        self.cfg["models"]["local"]["command"] = _exe(self.tmp / "fake-lms", FAKE_LMS)
        util.write_json(paths.CONFIG, self.cfg)

        self.claude_log = self.tmp / "claude.log"
        self.cfg["runner"]["command"] = _exe(self.tmp / "fake-claude", FAKE_CLAUDE)
        util.write_json(paths.CONFIG, self.cfg)

        self.env = mock.patch.dict(os.environ, {
            "FAKE_LMS_STATE": str(self.lms_state), "FAKE_LMS_LOG": str(self.lms_log),
            "FAKE_CLAUDE_LOG": str(self.claude_log), "ANTHROPIC_API_KEY": "sk-test-secret",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for k, v in self.saved.items():
            setattr(paths, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lms_loaded(self):
        return json.loads(self.lms_state.read_text())["loaded"]

    def lms_calls(self):
        return self.lms_log.read_text().splitlines() if self.lms_log.exists() else []

    def claude_calls(self):
        return [json.loads(l) for l in self.claude_log.read_text().splitlines()] if self.claude_log.exists() else []

    def new_job(self, job_id="issue-1", state_name="INTAKE"):
        st = state.new_state(job_id, 1, "t", "", ["factory"], self.cfg, 1)
        st["state"] = state_name
        state.save_state(st)
        state.job_dir(job_id).mkdir(parents=True, exist_ok=True)
        return st


class ResolveAndValidate(Env):
    def test_stage_entry_beats_group_entry_beats_default(self):
        profile = {"stages": {"review": "claude-sonnet-5-5", "review.security": "local:qwen3.6-35b-a3b-splash"}}
        self.assertEqual(models.resolve(profile, "review.security", "claude-haiku-5-5"), "local:qwen3.6-35b-a3b-splash")
        self.assertEqual(models.resolve(profile, "review.code", "claude-haiku-5-5"), "claude-sonnet-5-5")
        self.assertEqual(models.resolve(profile, "intake", "claude-haiku-5-5"), "claude-haiku-5-5")

    def test_unknown_stage_is_refused_and_the_master_is_not_a_stage(self):
        for bad in ["master", "session", "review.nobody", "orchestrator"]:
            with self.subTest(stage=bad), self.assertRaises(FactoryError):
                models.validate_stage(bad, self.cfg)

    def test_known_stages_are_accepted(self):
        for ok in ["intake", "spec", "build", "specialists", "review", "review.code"]:
            models.validate_stage(ok, self.cfg)

    def test_model_must_be_a_listed_claude_model_or_a_local_key(self):
        models.validate_model("claude-opus-5-5", self.cfg)
        models.validate_model("local:qwen3.6-35b-a3b-splash", self.cfg)
        with self.assertRaises(FactoryError):
            models.validate_model("gpt-9", self.cfg)
        with self.assertRaises(FactoryError):
            models.validate_model("local:", self.cfg)


class SettingTheProfile(Env):
    def test_set_stores_pairs_and_merges_with_the_previous_choice(self):
        models.set_stages(["build=local:qwen3.6-35b-a3b-splash"], self.cfg)
        models.set_stages(["review=claude-opus-5-5"], self.cfg)
        stages = models.load_profile()["stages"]
        self.assertEqual(stages, {"build": "local:qwen3.6-35b-a3b-splash", "review": "claude-opus-5-5"})

    def test_a_local_model_that_is_not_installed_is_refused(self):
        with self.assertRaisesRegex(FactoryError, "not installed in LM Studio: nope"):
            models.set_stages(["build=local:nope"], self.cfg)
        self.assertFalse(paths.PROFILE.exists())

    def test_malformed_pair_is_refused(self):
        with self.assertRaises(FactoryError):
            models.set_stages(["build"], self.cfg)

    def test_cli_profile_set_show_clear(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["profile", "set", "build=local:qwen3.6-35b-a3b-splash", "review.code=claude-sonnet-5-5"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["profile", "show"])
        self.assertIn("local:qwen3.6-35b-a3b-splash", out.getvalue())
        self.assertIn("claude-sonnet-5-5", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["profile", "clear"])
        self.assertFalse(paths.PROFILE.exists())

    def test_cli_profile_set_with_no_pairs_is_an_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["profile", "set"]), 1)


class LocalLoading(Env):
    def lm(self):
        return models.LMStudio(self.cfg)

    def test_loads_when_nothing_is_loaded(self):
        self.assertIn("loaded", self.lm().ensure_loaded("qwen3.6-35b-a3b-splash"))
        self.assertEqual(self.lms_loaded(), ["qwen3.6-35b-a3b-splash"])

    def test_keeps_the_model_when_it_is_already_loaded(self):
        util.write_json(self.lms_state, {"installed": ["qwen3.6-35b-a3b-splash", "qwen3.8-27b-splash"],
                                         "loaded": ["qwen3.6-35b-a3b-splash"]})
        self.lm().ensure_loaded("qwen3.6-35b-a3b-splash")
        self.assertFalse([c for c in self.lms_calls() if c.startswith(("load", "unload"))])

    def test_unloads_the_other_model_before_loading_the_wanted_one(self):
        util.write_json(self.lms_state, {"installed": ["qwen3.6-35b-a3b-splash", "qwen3.8-27b-splash"],
                                         "loaded": ["qwen3.6-35b-a3b-splash"]})
        self.lm().ensure_loaded("qwen3.8-27b-splash")
        calls = [c for c in self.lms_calls() if c.startswith(("load", "unload"))]
        self.assertEqual(calls, ["unload qwen3.6-35b-a3b-splash", "load qwen3.8-27b-splash -y"])
        self.assertEqual(self.lms_loaded(), ["qwen3.8-27b-splash"])

    def test_leaves_others_loaded_when_unload_others_is_off(self):
        util.write_json(self.lms_state, {"installed": ["qwen3.6-35b-a3b-splash", "qwen3.8-27b-splash"],
                                         "loaded": ["qwen3.6-35b-a3b-splash"]})
        self.cfg["models"]["local"]["unload_others"] = False
        models.LMStudio(self.cfg).ensure_loaded("qwen3.8-27b-splash")
        self.assertEqual(sorted(self.lms_loaded()), ["qwen3.6-35b-a3b-splash", "qwen3.8-27b-splash"])

    def test_a_model_that_fails_to_load_is_an_error(self):
        with self.assertRaises(FactoryError):
            self.lm().ensure_loaded("not-installed")

    def test_child_env_removes_the_real_key_and_points_at_lm_studio(self):
        env = self.lm().child_env({"ANTHROPIC_API_KEY": "sk-real", "HOME": "/x"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:9")
        self.assertEqual(env["HOME"], "/x")


class AgentsUseTheProfile(Env):
    def items(self, stage="intake", model="claude-sonnet-5-5", **extra):
        return [{"agent": "intake", "model": model, "tag": stage, "stage": stage, "output": "intake.md",
                 "extra": "", **extra}]

    def test_without_a_profile_the_config_model_runs(self):
        self.new_job()
        agents.execute("issue-1", self.items(), self.cfg, dry=False, out=lambda *a: None)
        self.assertEqual(self.claude_calls()[-1]["argv"][:3], ["-p", "--model", "claude-sonnet-5-5"])
        self.assertIsNone(self.claude_calls()[-1]["base"])

    def test_profile_sends_a_stage_to_a_local_model_with_the_key_withheld(self):
        self.new_job()
        models.set_stages(["intake=local:qwen3.6-35b-a3b-splash"], self.cfg)
        agents.execute("issue-1", self.items(), self.cfg, dry=False, out=lambda *a: None)
        call = self.claude_calls()[-1]
        self.assertEqual(call["argv"][:3], ["-p", "--model", "qwen3.6-35b-a3b-splash"])
        self.assertEqual(call["base"], "http://127.0.0.1:9")
        self.assertIsNone(call["api_key"], "the real Anthropic key must not reach the local server")
        self.assertEqual(self.lms_loaded(), ["qwen3.6-35b-a3b-splash"])

    def test_cloud_stage_keeps_its_environment(self):
        self.new_job()
        agents.execute("issue-1", self.items(), self.cfg, dry=False, out=lambda *a: None)
        self.assertEqual(self.claude_calls()[-1]["api_key"], "sk-test-secret")
        self.assertFalse([c for c in self.lms_calls() if c.startswith("load")])

    def test_an_explicit_model_flag_beats_the_profile(self):
        self.new_job()
        models.set_stages(["intake=local:qwen3.6-35b-a3b-splash"], self.cfg)
        agents.execute("issue-1", self.items(model="claude-haiku-5-5", pinned=True), self.cfg, dry=False,
                       out=lambda *a: None)
        self.assertEqual(self.claude_calls()[-1]["argv"][:3], ["-p", "--model", "claude-haiku-5-5"])

    def test_dry_run_names_the_profile_model_and_does_not_touch_lm_studio(self):
        self.new_job()
        models.set_stages(["intake=local:qwen3.8-27b-splash"], self.cfg)
        self.lms_log.unlink(missing_ok=True)  # only calls made by the dry run count
        lines = []
        agents.execute("issue-1", self.items(), self.cfg, dry=True, out=lines.append)
        self.assertIn("qwen3.8-27b-splash", " ".join(lines))
        self.assertFalse(self.lms_calls())
        self.assertFalse(self.claude_calls())


class StuckHandling(Env):
    def test_a_stuck_agent_is_retried_and_then_succeeds(self):
        self.new_job()
        with mock.patch.dict(os.environ, {
            "FAKE_LMS_STATE": str(self.lms_state), "FAKE_LMS_LOG": str(self.lms_log),
            "FAKE_CLAUDE_LOG": str(self.claude_log), "FAKE_CLAUDE_MODE": "hang_first",
        }):
            lines = []
            agents.execute("issue-1", [{"agent": "intake", "model": "claude-sonnet-5-5", "tag": "intake",
                                        "stage": "intake", "output": "intake.md"}],
                           self.cfg, dry=False, out=lines.append)
        self.assertEqual(len(self.claude_calls()), 2, "the stuck attempt and the retry")
        heal = (state.job_dir("issue-1") / "logs" / "self-heal.log").read_text()
        self.assertIn("attempt 1", heal)
        self.assertIn("no progress", heal)

    def test_when_every_attempt_is_stuck_the_stage_gives_up(self):
        self.new_job()
        with mock.patch.dict(os.environ, {
            "FAKE_LMS_STATE": str(self.lms_state), "FAKE_LMS_LOG": str(self.lms_log),
            "FAKE_CLAUDE_LOG": str(self.claude_log), "FAKE_CLAUDE_MODE": "hang",
        }):
            with self.assertRaises(watchdog.StuckAgent):
                agents.execute("issue-1", [{"agent": "intake", "model": "claude-sonnet-5-5", "tag": "intake",
                                            "stage": "intake", "output": "intake.md"}],
                               self.cfg, dry=False, out=lambda *a: None)
        self.assertEqual(len(self.claude_calls()), 2)

    def test_the_job_moves_to_needs_human_when_self_heal_gives_up(self):
        self.new_job()
        with mock.patch.dict(os.environ, {
            "FAKE_LMS_STATE": str(self.lms_state), "FAKE_LMS_LOG": str(self.lms_log),
            "FAKE_CLAUDE_LOG": str(self.claude_log), "FAKE_CLAUDE_MODE": "hang",
        }):
            steps.step_job("issue-1", self.cfg, dry=False, out=lambda *a: None)
        st = state.load_state("issue-1")
        self.assertEqual(st["state"], "NEEDS_HUMAN")
        self.assertIn("self-heal gave up", st["history"][-1]["note"])

    def test_a_stuck_local_model_is_retried_on_the_fallback_model(self):
        self.new_job()
        self.cfg["self_heal"]["fallback_model"] = "claude-sonnet-5-5"
        util.write_json(paths.CONFIG, self.cfg)
        models.set_stages(["intake=local:qwen3.6-35b-a3b-splash"], self.cfg)
        with mock.patch.dict(os.environ, {
            "FAKE_LMS_STATE": str(self.lms_state), "FAKE_LMS_LOG": str(self.lms_log),
            "FAKE_CLAUDE_LOG": str(self.claude_log), "FAKE_CLAUDE_MODE": "hang_first",
        }):
            agents.execute("issue-1", [{"agent": "intake", "model": "claude-sonnet-5-5", "tag": "intake",
                                        "stage": "intake", "output": "intake.md"}],
                           self.cfg, dry=False, out=lambda *a: None)
        self.assertEqual(self.claude_calls()[0]["argv"][2], "qwen3.6-35b-a3b-splash")
        self.assertEqual(self.claude_calls()[1]["argv"][2], "claude-sonnet-5-5")


class ReportAndDoctor(Env):
    def test_profile_table_shows_every_stage_and_reviewer(self):
        models.set_stages(["build=local:qwen3.6-35b-a3b-splash", "review.security=claude-opus-5-5"], self.cfg)
        text = report.profile(self.cfg)
        self.assertIn("build", text)
        self.assertIn("local:qwen3.6-35b-a3b-splash", text)
        self.assertIn("review.security", text)
        self.assertIn("claude-opus-5-5", text)

    def test_models_list_marks_the_loaded_model(self):
        util.write_json(self.lms_state, {"installed": ["qwen3.6-35b-a3b-splash"], "loaded": ["qwen3.6-35b-a3b-splash"]})
        text = report.models_list(self.cfg)
        self.assertIn("local:qwen3.6-35b-a3b-splash   (loaded now)", text)
        self.assertIn("claude-opus-5-5", text)


class ConfigKeysAreEditable(unittest.TestCase):
    def test_self_heal_keys_can_be_changed_from_the_cli(self):
        from cf import config
        self.assertIn("self_heal.enabled", config.EDITABLE)
        self.assertIn("self_heal.stuck_after_s", config.EDITABLE)


if __name__ == "__main__":
    unittest.main()
