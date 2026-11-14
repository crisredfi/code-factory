"""The gate refuses local tool state in a branch's commits (.claude/, build output, Finder metadata).

Regression: a Doomail job committed .claude/ce-memory.db and .claude/memory/ce-summary.md, because the
builder ran `git add -A`. The gate now names those files, and the builder is told never to stage them.

Run: python3 -m unittest discover -s CodeFactory/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from cf import gate, config  # noqa: E402

PATTERNS = [".claude/", ".build/", "DerivedData/", ".swiftpm/", ".DS_Store"]


class LocalState(unittest.TestCase):
    def test_agent_memory_and_database_under_claude_are_local_state(self):
        self.assertTrue(gate.is_local_state(".claude/ce-memory.db", PATTERNS))
        self.assertTrue(gate.is_local_state(".claude/memory/ce-summary.md", PATTERNS))

    def test_folder_patterns_match_at_any_depth(self):
        self.assertTrue(gate.is_local_state("App/DerivedData/x.plist", PATTERNS))

    def test_finder_metadata_matches_by_name_anywhere(self):
        self.assertTrue(gate.is_local_state("App/Reader/.DS_Store", PATTERNS))

    def test_ordinary_source_is_not_local_state(self):
        for path in ["App/Reader/ReaderView.swift", "docs/claude.md", "claude-notes.txt", "DerivedDataHelper.swift"]:
            self.assertFalse(gate.is_local_state(path, PATTERNS), path)

    def test_the_committed_config_names_the_paths(self):
        cfg = config.load()
        for p in [".claude/", ".DS_Store"]:
            self.assertIn(p, cfg.get("local_state_paths", []))


if __name__ == "__main__":
    unittest.main()
