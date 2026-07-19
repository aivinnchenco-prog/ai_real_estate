#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from montage_lock import MontageBusyError, montage_file_lock  # noqa: E402
from notion_gate import NotionListing, try_claim_montage  # noqa: E402


class MontageLockTests(unittest.TestCase):
    def test_file_lock_blocks_second_holder(self):
        with montage_file_lock("A_001", blocking=False):
            with self.assertRaises(MontageBusyError) as ctx:
                with montage_file_lock("A_002", blocking=False):
                    pass
            self.assertIn("A_001", str(ctx.exception))

    def test_try_claim_rejects_other_in_progress(self):
        crm = MagicMock()
        statuses = {
            "after_structurize": "ready_for_video",
            "video_failed": "video_failed",
            "video_start": "video_in_progress",
        }
        fields = {"status": "Статус", "object_id": "Объект ID"}
        listing = NotionListing(
            "p2", "A_002", "ready_for_video", "T", "https://g", None, None,
            montage_flag="ДА", video_engine="Wan 2.7",
        )
        busy = NotionListing(
            "p1", "A_001", "video_in_progress", "T", "https://g", None, None
        )
        crm.query_by_status.return_value = [{"id": "p1", "properties": {}}]

        from notion_gate import fetch_montage_in_progress, parse_listing_page

        # patch fetch via real parse — simpler to test try_claim with manual busy simulation
        def fake_fetch(c, st, f):
            return busy

        import notion_gate as ng

        orig = ng.fetch_montage_in_progress
        ng.fetch_montage_in_progress = fake_fetch
        try:
            ok, reason = try_claim_montage(crm, listing, statuses, fields)
        finally:
            ng.fetch_montage_in_progress = orig

        self.assertFalse(ok)
        self.assertIn("A_001", reason)


if __name__ == "__main__":
    unittest.main()
