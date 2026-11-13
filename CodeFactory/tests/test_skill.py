"""The skill names commands and settings. Every one of them must exist in the controller, or the skill is wrong."""
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CF_ROOT = HERE.parent
sys.path.insert(0, str(CF_ROOT / "scripts"))
from cf import cli, config  # noqa: E402

SKILL = (CF_ROOT / "skills" / "code-factory" / "SKILL.md").read_text()


class SkillMatchesController(unittest.TestCase):
    def test_every_command_the_skill_runs_is_a_real_subcommand(self):
        parser = cli.build_parser()
        subcommands = set(parser._subparsers._group_actions[0].choices)  # argparse's registry of subcommands
        used = set(re.findall(r"factory\.py ([a-z]+)", SKILL))
        self.assertTrue(used, "the skill should name commands")
        missing = used - subcommands
        self.assertFalse(missing, f"skill names commands the controller does not have: {sorted(missing)}")

    def test_every_setting_the_skill_mentions_is_editable(self):
        mentioned = set(re.findall(r"config set ([a-z_.]+)", SKILL))
        mentioned |= {"max_parallel_jobs", "max_parallel_subtasks", "max_rounds", "reviewers.defaults",
                      "auto_merge.enabled", "auto_merge.max_risk", "github.comments"}
        missing = [k for k in mentioned if k not in config.EDITABLE]
        self.assertFalse(missing, f"skill mentions settings that are not editable: {missing}")

    def test_every_state_in_the_pipeline_table_is_a_real_state(self):
        from cf.model import COLUMNS
        names = set(re.findall(r"^\| ([A-Z_]+) \|", SKILL, re.M))
        unknown = {n for n in names if n not in COLUMNS and n not in {"STATE", "FAILED"}}
        self.assertFalse(unknown, f"skill names states the controller does not have: {sorted(unknown)}")

    def test_frontmatter_has_name_and_description(self):
        self.assertTrue(SKILL.startswith("---\nname: code-factory\ndescription: "))


if __name__ == "__main__":
    unittest.main()
