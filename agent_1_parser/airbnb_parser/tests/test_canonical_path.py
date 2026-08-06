"""Canonical Airbnb parser path and import smoke tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


CANONICAL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CANONICAL_ROOT.parents[1]


class TestCanonicalAirbnbParser(unittest.TestCase):
    def setUp(self) -> None:
        if str(CANONICAL_ROOT) not in sys.path:
            sys.path.insert(0, str(CANONICAL_ROOT))

    def test_canonical_path_exists(self) -> None:
        self.assertTrue((CANONICAL_ROOT / "main.py").is_file())
        self.assertTrue((CANONICAL_ROOT / "workflow.py").is_file())
        self.assertTrue((CANONICAL_ROOT / "agent2_handoff.py").is_file())

    def test_main_entrypoint_file(self) -> None:
        content = (CANONICAL_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("if __name__", content)
        self.assertIn("main", content.lower())

    def test_import_workflow(self) -> None:
        import workflow  # noqa: F401

    def test_import_agent2_handoff(self) -> None:
        import agent2_handoff  # noqa: F401

    def test_no_legacy_airbnb_scraper_dir(self) -> None:
        self.assertFalse((REPO_ROOT / "agent_1_parser" / "airbnb_scraper").exists())


if __name__ == "__main__":
    unittest.main()
