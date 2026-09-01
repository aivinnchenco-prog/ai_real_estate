#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from listing_parser import parse_listing  # noqa: E402


class ListingPriceGuardTests(unittest.TestCase):
    def test_nbsp_monthly_price_still_parses(self):
        text = "Вилла 2BR\nЦена: 10\u00a0000\u00a0฿ в месяц\nRawai"
        d = parse_listing(text, districts=["Rawai"])
        self.assertEqual(d.price_monthly, 10000.0)

    def test_kwh_is_not_monthly_price(self):
        text = (
            "Вилла 2BR Laguna\n"
            "Плата за электричество составляет 7 батов за кВт⋅ч.\n"
            "При заселении взимается депозит за сохранность имущества "
            "в размере 10 000 батов."
        )
        d = parse_listing(text, districts=["Laguna"])
        self.assertIsNone(d.price_monthly)
        self.assertEqual(d.deposit, 10000.0)

    def test_deposit_in_the_amount_of(self):
        text = "При заезде залог за сохранность в размере до 30 000 батов."
        d = parse_listing(text, districts=["Thalang"])
        self.assertEqual(d.deposit, 30000.0)
        self.assertIsNone(d.price_monthly)

    def test_legendary_usd_deposit_still_converts(self):
        text = "TITLE LEGENDARY 2BR\nВозвратный депозит 200 USD\nWi-Fi, бассейн"
        d = parse_listing(text, districts=["Choeng Thale"])
        self.assertEqual(d.deposit, 7200.0)
        self.assertIsNone(d.price_monthly)

    def test_bare_thb_without_month_word_still_parses(self):
        text = "Вилла 2BR Rawai\n35 000 ฿\nБассейн, Wi-Fi"
        d = parse_listing(text, districts=["Rawai"])
        self.assertEqual(d.price_monthly, 35000.0)

    def test_pomesyachno_is_rent_context(self):
        text = (
            "Дом целиком, Тхаланг, Таиланд 6 гостей\n"
            "💰 261 954 ฿ помесячно (2026-12-01 — 2027-03-31)"
        )
        d = parse_listing(text, districts=["Thalang"])
        self.assertEqual(d.price_monthly, 261954.0)

    def test_thb_slash_month_still_parses(self):
        text = "POOL VILLA FOR RENT\n200,000 THB/Month\nKoh Kaew, Phuket"
        d = parse_listing(text, districts=["Koh Kaew"])
        self.assertEqual(d.price_monthly, 200000.0)


if __name__ == "__main__":
    unittest.main()
