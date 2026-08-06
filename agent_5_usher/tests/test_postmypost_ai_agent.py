#!/usr/bin/env python3
"""Offline tests for Agent 5 PostMyPost AI agent boundary."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from postmypost_ai_agent import (  # noqa: E402
    NOT_CONFIGURED_WARNING,
    STATUS_BLOCKED,
    integration_ready,
    missing_integration_contract,
    queue_postmypost_ai_agent,
)


class PostMyPostAiAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        import postmypost_ai_agent as mod

        self._state_file = Path(self._testMethodName + ".json")
        self._orig = mod._STATE_FILE
        mod._STATE_FILE = self._state_file
        if self._state_file.exists():
            self._state_file.unlink()

    def tearDown(self) -> None:
        import postmypost_ai_agent as mod

        mod._STATE_FILE = self._orig
        if self._state_file.exists():
            self._state_file.unlink()

    def test_integration_not_ready(self) -> None:
        self.assertFalse(integration_ready())
        contract = missing_integration_contract()
        self.assertIn("endpoint", contract)
        self.assertIn("unknown", contract["endpoint"])

    def test_queue_records_blocked_with_warning(self) -> None:
        out = queue_postmypost_ai_agent(
            page_id="page-1",
            object_id="20260805_001",
            publication_id="pub-1",
            post_url="https://www.instagram.com/p/abc/",
            platform="instagram",
        )
        self.assertTrue(out["ok"])
        self.assertFalse(out["skipped"])
        self.assertEqual(out["status"], STATUS_BLOCKED)
        self.assertEqual(out["warning"], NOT_CONFIGURED_WARNING)

    def test_repeat_queue_is_skipped(self) -> None:
        first = queue_postmypost_ai_agent(
            page_id="page-1",
            object_id="20260805_001",
            publication_id="pub-1",
            platform="instagram",
        )
        second = queue_postmypost_ai_agent(
            page_id="page-1",
            object_id="20260805_001",
            publication_id="pub-1",
            platform="instagram",
        )
        self.assertFalse(first["skipped"])
        self.assertTrue(second["skipped"])
        self.assertEqual(second["status"], STATUS_BLOCKED)


if __name__ == "__main__":
    unittest.main()
