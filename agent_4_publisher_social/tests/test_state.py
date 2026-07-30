from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from publisher_social import state


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "state.json"
        self.path_patch = patch("publisher_social.state.state_path", return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_mark_done_and_daily_increment_are_idempotent(self) -> None:
        snapshot = state.load_state()
        first = state.mark_channel_done(
            snapshot,
            "object-1",
            "tiktok",
            status="verified",
            post_url="https://www.tiktok.com/example",
            daily_date="2026-07-29",
        )
        second = state.mark_channel_done(
            snapshot,
            "object-1",
            "tiktok",
            status="verified",
            daily_date="2026-07-29",
        )

        self.assertTrue(first)
        self.assertFalse(second)
        current = state.load_state()
        self.assertEqual(state.daily_count(current, "2026-07-29", "tiktok"), 1)
        self.assertEqual(
            current["objects"]["object-1"]["channels_done"]["tiktok"]["status"],
            "verified",
        )

    def test_parallel_mutations_do_not_lose_log_entries(self) -> None:
        def write(index: int) -> None:
            state.append_log(state.load_state(), "object-1", f"entry-{index}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(40)))

        logs = state.load_state()["objects"]["object-1"]["log"]
        self.assertEqual(len(logs), 40)
        for index in range(40):
            self.assertTrue(any(f"entry-{index}" in line for line in logs))

    def test_failed_atomic_replace_keeps_previous_json(self) -> None:
        original = {"objects": {"safe": {"channels_done": {}, "log": []}}, "daily": {}}
        state.save_state(original)

        with patch("publisher_social.state.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                state.save_state({"objects": {"broken": {}}, "daily": {}})

        parsed = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("safe", parsed["objects"])
        self.assertNotIn("broken", parsed["objects"])

    def test_outbox_survives_failure_metadata_and_can_be_cleared(self) -> None:
        snapshot = state.load_state()
        state.enqueue_notion_update(
            snapshot,
            key="page:tiktok",
            page_id="page",
            object_id="object",
            channel="tiktok",
            properties={"post_url": {"url": "https://example.invalid"}},
        )
        state.mark_notion_update_failed(snapshot, "page:tiktok", "temporary")

        pending = state.pending_notion_updates(state.load_state())
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["attempts"], 1)
        state.clear_notion_update(snapshot, "page:tiktok")
        self.assertEqual(state.pending_notion_updates(state.load_state()), [])


if __name__ == "__main__":
    unittest.main()
