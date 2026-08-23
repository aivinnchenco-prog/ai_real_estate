#!/usr/bin/env python3
"""Unit tests for maps_resolver safety gates (no network)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from maps_resolver import (  # noqa: E402
    _looks_like_complex,
    build_search_query,
    district_from_coords,
    place_matches_district,
    resolve_google_maps,
)


class MapsResolverSafetyTest(unittest.TestCase):
    def test_cyrillic_villa_not_complex(self):
        title = "Вилла, отдельный бассейн, бесплатный трансфер при бронировании на 7 дней и более"
        self.assertFalse(_looks_like_complex(title))

    def test_query_falls_back_to_district(self):
        title = "Вилла, отдельный бассейн, бесплатный трансфер"
        q = build_search_query(
            complex_name=None,
            district="Си Сунтон",
            location_line=None,
            title=title,
        )
        self.assertEqual(q, "Си Сунтон Phuket Thailand")
        self.assertNotIn("Вилла", q)

    def test_si_sunthon_centroid(self):
        # listing point near Si Sunthon inland
        name = district_from_coords(8.00567, 98.34456)
        self.assertEqual(name, "Si Sunthon")

    def test_place_matches_district_aliases(self):
        self.assertTrue(
            place_matches_district(
                "Tambon Si Sunthon, Amphoe Thalang, Phuket",
                "Си Сунтон",
            )
        )
        self.assertFalse(
            place_matches_district(
                "77 1 Tambon Ratsada, Amphoe Mueang Phuket",
                "Си Сунтон",
            )
        )

    def test_no_places_without_coords_or_complex(self):
        fake = {
            "url": "https://maps.google.com/?cid=bad",
            "place_id": "x",
            "place_name": "Koh Sirey Beachfront Pool Villa",
            "formatted_address": "Tambon Ratsada, Phuket",
            "location": (7.88, 98.43),
        }
        with mock.patch("maps_resolver._places_api_resolve", return_value=fake):
            os.environ["GOOGLE_MAPS_API_KEY"] = "test-key"
            try:
                result = resolve_google_maps(
                    "Дом целиком, Си Сунтон, Таиланд\nВилла с бассейном",
                    "Си Сунтон",
                    "Вилла, отдельный бассейн, бесплатный трансфер",
                    coords=None,
                )
            finally:
                os.environ.pop("GOOGLE_MAPS_API_KEY", None)
        self.assertEqual(result.method, "search_url")
        self.assertNotIn("cid=bad", result.url)
        self.assertIn("Phuket", result.url)

    def test_coords_win_over_far_place(self):
        fake = {
            "url": "https://maps.google.com/?cid=bad",
            "place_id": "x",
            "place_name": "Koh Sirey Beachfront Pool Villa",
            "formatted_address": "Tambon Ratsada, Phuket",
            "location": (7.88, 98.43),
        }
        coords = (8.00567, 98.34456)
        with mock.patch("maps_resolver._places_api_resolve", return_value=fake):
            os.environ["GOOGLE_MAPS_API_KEY"] = "test-key"
            try:
                result = resolve_google_maps(
                    "Си Сунтон",
                    "Си Сунтон",
                    "Вилла",
                    coords=coords,
                )
            finally:
                os.environ.pop("GOOGLE_MAPS_API_KEY", None)
        self.assertEqual(result.method, "coords_point")
        self.assertIn("8.005670", result.url)
        self.assertIn("98.344560", result.url)


if __name__ == "__main__":
    unittest.main()
