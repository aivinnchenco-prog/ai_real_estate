#!/usr/bin/env python3
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from housing_type import detect_housing_type  # noqa: E402
from listing_parser import parse_listing  # noqa: E402
from session_store import bind_object_id, get_object_id, load_session  # noqa: E402
from notion_gate import NotionListing, agent3_ready, agent6_ready, agent4_ready  # noqa: E402
from agent2_structurize import (  # noqa: E402
    first_upcoming_month_entry,
    monthly_price_options,
    apply_parsed_meta,
)


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
        # Залог всегда в THB: 200 USD × 36 (fx по умолчанию) = 7200
        self.assertEqual(d.deposit, 7200.0)
        self.assertEqual(d.rent_type, "Краткосрочная")
        self.assertIn("Бассейн", d.amenities)
        self.assertEqual(d.view, "Бассейн")

    def test_max_guests_from_text(self):
        d = parse_listing("Вилла 3BR, до 6 гостей, бассейн", districts=["Rawai"])
        self.assertEqual(d.max_guests, 6)
        d2 = parse_listing("Condo 2BR, sleeps 4", districts=["Rawai"])
        self.assertEqual(d2.max_guests, 4)
        d3 = parse_listing("Вилла без вместимости, в 10 минутах от пляжа", districts=["Rawai"])
        self.assertIsNone(d3.max_guests)

    def test_price_with_nbsp(self):
        """Airbnb часто пишет «10 000 ฿» с неразрывными пробелами — не должны падать."""
        text = "Вилла 2BR\nЦена: 10\u00a0000\u00a0฿ в месяц\nRawai"
        d = parse_listing(text, districts=["Rawai"])
        self.assertEqual(d.price_monthly, 10000.0)

class ChainGateTests(unittest.TestCase):
    def test_agent3_requires_gallery(self):
        st = {"after_structurize": "ready_for_video", "video_failed": "video_failed", "video_done": "ready_to_post"}
        listing = NotionListing("p1", "20260701_001", "ready_for_video", "T", None, None, None)
        ok, reason = agent3_ready(listing, st)
        self.assertFalse(ok)
        listing2 = NotionListing("p1", "20260701_001", "ready_for_video", "T", "https://x/gallery", None, None,
                                 montage_flag="ДА", video_engine="Wan 2.7")
        ok2, _ = agent3_ready(listing2, st)
        self.assertTrue(ok2)

    def test_agent6_requires_video(self):
        st = {"video_done": "ready_to_post"}
        listing = NotionListing("p1", "id", "ready_to_post", "T", "https://g", None, None)
        self.assertFalse(agent6_ready(listing, st)[0])
        listing2 = NotionListing("p1", "id", "ready_to_post", "T", "https://g", None, "https://v",
                                 publish_flag="ДА", montage_flag="ДА")
        self.assertTrue(agent6_ready(listing2, st)[0])
        listing3 = NotionListing("p1", "id", "ready_to_post", "T", "https://g", "https://seedance", None,
                                 publish_flag="ДА")
        self.assertTrue(agent6_ready(listing3, st)[0])

    def test_montage_flag_gates_agent3(self):
        """Агент 3 только при явном «Монтаж»=ДА; пусто и НЕТ — пропуск."""
        st = {
            "after_structurize": "ready_for_video",
            "video_failed": "video_failed",
            "video_start": "video_in_progress",
        }
        listing = NotionListing("p1", "id", "ready_for_video", "T", "https://g", None, None,
                                montage_flag="НЕТ")
        ok, reason = agent3_ready(listing, st)
        self.assertFalse(ok)
        self.assertIn("монтаж выключен", reason)
        empty = NotionListing("p1", "id", "ready_for_video", "T", "https://g", None, None)
        self.assertFalse(agent3_ready(empty, st)[0])  # пусто = выключено
        yes = NotionListing("p1", "id", "ready_for_video", "T", "https://g", None, None,
                            montage_flag="ДА", video_engine="Seedance 2.0")
        self.assertTrue(agent3_ready(yes, st)[0])
        busy = NotionListing("p1", "id", "video_in_progress", "T", "https://g", None, None,
                             montage_flag="ДА", video_engine="Wan 2.7")
        ok_busy, reason_busy = agent3_ready(busy, st)
        self.assertFalse(ok_busy)
        self.assertIn("in progress", reason_busy)

    def test_agent3_requires_video_engine_when_montage_on(self):
        st = {
            "after_structurize": "ready_for_video",
            "video_failed": "video_failed",
            "video_start": "video_in_progress",
        }
        listing = NotionListing("p1", "id", "ready_for_video", "T", "https://g", None, None,
                                montage_flag="ДА")
        ok, reason = agent3_ready(listing, st)
        self.assertFalse(ok)
        self.assertIn("видео-движок", reason)
        with_engine = NotionListing("p1", "id", "ready_for_video", "T", "https://g", None, None,
                                    montage_flag="ДА", video_engine="Seedance 2.0")
        self.assertTrue(agent3_ready(with_engine, st)[0])

    def test_publish_flag_gates_agent6(self):
        """«Публикация» = НЕТ — постинг выключен даже при готовом видео."""
        st = {"video_done": "ready_to_post"}
        listing = NotionListing("p1", "id", "ready_to_post", "T", "https://g",
                                None, "https://v", publish_flag="НЕТ")
        ok, reason = agent6_ready(listing, st)
        self.assertFalse(ok)
        self.assertIn("Публикация", reason)

    def test_phone_mode_ignores_stale_error_count(self):
        """При phone publisher старый Metricool error_count не блокирует."""
        st = {"video_done": "ready_to_post"}
        listing = NotionListing(
            "p1", "id", "ready_to_post", "T", "https://g",
            None, "https://v", publish_flag="ДА", error_count=1033,
        )
        self.assertFalse(agent6_ready(listing, st)[0])
        self.assertTrue(agent6_ready(listing, st, ignore_error_count=True)[0])

    def test_agent6_carousel_without_video_when_montage_off(self):
        """Монтаж=НЕТ + Публикация=ДА — публикуем карусель, видео не требуем."""
        st = {"video_done": "ready_to_post"}
        listing = NotionListing("p1", "id", "ready_to_post", "T", "https://g", None, None,
                                montage_flag="НЕТ", publish_flag="ДА")
        ok, reason = agent6_ready(listing, st)
        self.assertTrue(ok)
        self.assertIn("карусель", reason)
        no_gallery = NotionListing("p1", "id", "ready_to_post", "T", None, None, None,
                                   montage_flag="НЕТ", publish_flag="ДА")
        self.assertFalse(agent6_ready(no_gallery, st)[0])


    def test_one_id_per_session(self):
        sid = "_test_session_bind"
        bind_object_id(sid, "20260701_099")
        self.assertEqual(get_object_id(sid), "20260701_099")
        bind_object_id(sid, "20260701_099")
        self.assertEqual(load_session(sid)["object_id"], "20260701_099")
        (ROOT / "data" / "sessions" / sid / "session.json").unlink(missing_ok=True)


class MonthlyPricesTests(unittest.TestCase):
    def test_first_upcoming_skips_current_month(self):
        monthly = {
            "2026-07": {"price": 40000, "status": "monthly"},
            "2026-08": {"price": 55000, "status": "monthly"},
            "2026-09": {"price": 60000, "status": "prorated"},
        }
        key, entry = first_upcoming_month_entry(monthly, today=date(2026, 7, 16))
        self.assertEqual(key, "2026-08")
        self.assertEqual(entry["price"], 55000)

    def test_options_skip_null_prices(self):
        monthly = {
            "2026-08": {"price": None, "status": "insufficient_data"},
            "2026-09": {"price": 99200, "status": "monthly"},
        }
        self.assertEqual(monthly_price_options(monthly), ["2026-09 · 99 200 ฿"])

    def test_apply_sets_price_monthly_from_upcoming(self):
        props = {}
        nf = {"monthly_prices": "monthly_prices", "price_monthly": "Цена за месяц"}
        apply_parsed_meta(
            props,
            {
                "monthly_prices": {
                    "2026-08": {"price": 55000, "status": "monthly"},
                    "2026-09": {"price": 60000, "status": "monthly"},
                }
            },
            nf,
            today=date(2026, 7, 16),
        )
        self.assertIn("monthly_prices", props)
        self.assertEqual(props["Цена за месяц"]["number"], 55000.0)


if __name__ == "__main__":
    unittest.main()
