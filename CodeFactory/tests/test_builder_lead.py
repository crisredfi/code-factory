"""The build lead and the specialist catalog it deploys.

Regression: the plan prompt said "pick specialists from the catalog in your prompt" but the catalog was
never injected, so the model invented `factory-builder` as a specialist. These tests check the catalog
reaches the prompt, every routed name has a definition, and the lead is the build agent.

Run: python3 -m unittest discover -s CodeFactory/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from cf import agents, config, paths  # noqa: E402


class CatalogReachesThePlanPrompt(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def plan_extra(self):
        items = agents.stage_items("build", self.cfg, {"round": 1, "round_limit": 3}, "plan")
        self.assertEqual(len(items), 1)
        return items[0]["extra"]

    def test_every_specialist_name_is_in_the_plan_prompt(self):
        extra = self.plan_extra()
        for name in self.cfg["agents"]["catalog"]:
            self.assertIn(f"- {name}:", extra, f"{name} missing from the plan prompt")

    def test_each_specialist_has_its_routing_line(self):
        extra = self.plan_extra()
        self.assertEqual(extra.count("use when:"), len(self.cfg["agents"]["catalog"]))

    def test_the_catalog_block_is_the_only_list_offered(self):
        block = agents.catalog_block(self.cfg)
        self.assertTrue(block.startswith("Specialist catalog"))
        self.assertNotIn("factory-builder", block)


class LeadAndSpecialistsExist(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_build_stage_runs_the_factory_builder_lead(self):
        self.assertEqual(self.cfg["stages"]["build"]["agent"], "factory-builder")
        self.assertTrue((paths.AGENTS / "factory-builder.md").exists())

    def test_old_builder_definition_is_gone(self):
        self.assertFalse((paths.AGENTS / "builder.md").exists())

    def test_every_catalog_specialist_has_a_definition_and_a_route(self):
        for name, spec in self.cfg["agents"]["catalog"].items():
            self.assertTrue((paths.AGENTS / f"{name}.md").exists(), name)
            self.assertTrue(spec.get("use_when"), f"{name} has no use_when")

    def test_the_lead_definition_names_the_catalog_rule_and_the_proposal_path(self):
        text = (paths.AGENTS / "factory-builder.md").read_text()
        self.assertIn("Specialist catalog", text)
        self.assertIn("build/proposed-specialist.md", text)
        self.assertIn("do not invent one", text)


if __name__ == "__main__":
    unittest.main()
