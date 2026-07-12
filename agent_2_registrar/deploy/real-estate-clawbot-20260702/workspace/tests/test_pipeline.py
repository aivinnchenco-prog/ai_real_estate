#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from housing_type import detect_housing_type  # noqa: E402
from listing_parser import parse_listing  # noqa: E402
from session_store import bind_object_id, get_object_id, load_session  # noqa: E402
from notion_gate import NotionListing, agent3_ready, agent6_ready, agent4_ready  # noqa: E402


LEGENDARY = """
TITLE LEGENDARY 2BR с видом на бассейн / БАНГТАО
The Title Legendary Bang-Tao — современный курортный комплекс
Возвратный депозит 200 USD
Wi-Fi, бассейн, фитнес, детские игровые комнаты
"""


class HousingTypeTests(unittest.TestCase):
    def test_legendary_is_condo(self):
        ht = detect_housing_type(LEGENDARY, title="TITLE LEGENDARY 2BR")
        self.assertEqual(ht, "Кондоминиум")

    def test_villa(self):
        ht = detect_housing_type("Private pool villa 4BR in Rawai", title="Villa")
        self.assertEqual(ht, "Вилла")


class ListingParserTests(unittest.TestCase):
    def test_deposit_and_amenities(self):
        d = parse_listing(LEGENDARY, districts=["Choeng Thale"], source="Airbnb https://x.com")
        self.assertEqual(d.rooms, 2)
        self.assertEqual(d.deposit, 200.0)
        self.assertEqual(d.rent_type, "Краткосрочная")
        self.assertIn("Бассейн", d.amenities)
        self.assertEqual(d.view, "Бассейн")

class ChainGateTests(unittest.TestCase):
    def test_agent3_requires_gallery(self):
        st = {"after_structurize": "ready_for_video", "video_failed": "video_failed", "video_done": "ready_to_post"}
        listing = NotionListing("p1", "20260701_001", "ready_for_video", "T", None, None, None, None, None)
        ok, reason = agent3_ready(listing, st)
        self.assertFalse(ok)
        listing2 = NotionListing("p1", "20260701_001", "ready_for_video", "T", "https://x/gallery", None, None, None, None)
        ok2, _ = agent3_ready(listing2, st)
        self.assertTrue(ok2)

    def test_agent6_requires_video(self):
        st = {"video_done": "ready_to_post"}
        listing = NotionListing("p1", "id", "ready_to_post", "T", "https://g", None, None, None, None)
        self.assertFalse(agent6_ready(listing, st)[0])
        listing2 = NotionListing("p1", "id", "ready_to_post", "T", "https://g", "https://v", None, None, None)
        self.assertTrue(agent6_ready(listing2, st)[0])
        self.assertTrue(agent4_ready(listing2, st)[0])


    def test_one_id_per_session(self):
        sid = "_test_session_bind"
        bind_object_id(sid, "20260701_099")
        self.assertEqual(get_object_id(sid), "20260701_099")
        bind_object_id(sid, "20260701_099")
        self.assertEqual(load_session(sid)["object_id"], "20260701_099")
        (ROOT / "data" / "sessions" / sid / "session.json").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
