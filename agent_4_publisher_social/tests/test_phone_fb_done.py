from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from publisher_social import pipeline, state


def _page(*, groups_done: bool | None = None, marketplace_done: bool | None = None) -> dict:
    props: dict = {
        "Объект ID": {"type": "rich_text", "rich_text": [{"plain_text": "A_20260811_001"}]},
    }
    if groups_done is not None:
        props["phone_fb_groups_done"] = {"type": "checkbox", "checkbox": groups_done}
    if marketplace_done is not None:
        props["phone_fb_marketplace_done"] = {
            "type": "checkbox",
            "checkbox": marketplace_done,
        }
    return {"id": "page-1", "properties": props}


def _config() -> dict:
    return {
        "channels": ["fb_groups", "fb_marketplace"],
        "notion": {
            "fields": {"object_id": "Объект ID"},
            "phone_lock_fields": {
                "fb_groups_done": "phone_fb_groups_done",
                "fb_marketplace_done": "phone_fb_marketplace_done",
            },
        },
    }


class PhoneFbDoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self.state_patch = patch(
            "publisher_social.state.state_path",
            return_value=self.state_path,
        )
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_unchecked_columns_keep_channels_pending(self) -> None:
        pending = pipeline.pending_channels_for_page(
            _page(groups_done=False, marketplace_done=False),
            object_id="A_20260811_001",
            config=_config(),
        )
        self.assertEqual(pending, ["fb_groups", "fb_marketplace"])

    def test_checked_column_skips_channel(self) -> None:
        pending = pipeline.pending_channels_for_page(
            _page(groups_done=True, marketplace_done=False),
            object_id="A_20260811_001",
            config=_config(),
        )
        self.assertEqual(pending, ["fb_marketplace"])
        done = state.load_state()["objects"]["A_20260811_001"]["channels_done"]
        self.assertIn("fb_groups", done)

    def test_unchecking_done_column_clears_local_state_for_republish(self) -> None:
        st = state.load_state()
        state.mark_channel_done(st, "A_20260811_001", "fb_groups", status="accepted")
        pending = pipeline.pending_channels_for_page(
            _page(groups_done=False, marketplace_done=True),
            object_id="A_20260811_001",
            config=_config(),
        )
        self.assertEqual(pending, ["fb_groups"])
        done = state.load_state()["objects"]["A_20260811_001"].get("channels_done", {})
        self.assertNotIn("fb_groups", done)

    def test_try_write_phone_done_enqueues_notion_update(self) -> None:
        from publisher_social.models import PublishJob

        job = PublishJob(
            page_id="page-1",
            object_id="A_20260811_001",
            title="t",
            caption_social="",
            caption_fb="",
        )
        with patch("publisher_social.pipeline.retry_pending_notion_updates", return_value=(1, 0)):
            pipeline._try_write_phone_done(job, "fb_groups", done=True)
        pending = state.load_state().get("notion_outbox") or {}
        self.assertTrue(any("phone_done" in key for key in pending))


if __name__ == "__main__":
    unittest.main()
