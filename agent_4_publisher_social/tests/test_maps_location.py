from __future__ import annotations

import unittest
from unittest.mock import patch

from publisher_social.maps_location import (
    district_from_google_maps_url,
    marketplace_location_query,
    parse_district_from_maps_label,
    place_from_maps_url_path,
)


class MapsLocationTests(unittest.TestCase):
    def test_parse_district_strips_plus_code(self) -> None:
        self.assertEqual(
            parse_district_from_maps_label(
                "X8R4+HWJ Choeng Thale, Thalang District, Пхукет"
            ),
            "Choeng Thale",
        )
        self.assertEqual(
            parse_district_from_maps_label(
                "plus code: X8R4+HWJ Choeng Thale, Thalang District, Phuket"
            ),
            "Choeng Thale",
        )

    def test_place_from_maps_url_path(self) -> None:
        url = (
            "https://www.google.com/maps/place/Choeng+Thale,+Thalang+District,+Phuket/"
            "@8.0,98.3,15z"
        )
        self.assertEqual(place_from_maps_url_path(url), "Choeng Thale")

    def test_marketplace_location_query_prefers_google_maps_path(self) -> None:
        url = "https://www.google.com/maps/place/Bang+Tao,+Phuket/"
        self.assertEqual(
            marketplace_location_query(google_maps_url=url, district_fallback="Old District"),
            "Bang Tao, Phuket",
        )

    def test_marketplace_location_query_falls_back_to_district(self) -> None:
        self.assertEqual(
            marketplace_location_query(google_maps_url=None, district_fallback="Choeng Thale"),
            "Choeng Thale, Phuket",
        )

    def test_query_from_maps_search_url(self) -> None:
        from publisher_social.maps_location import query_from_maps_search_url

        url = "https://www.google.com/maps/search/Choeng%20Thale%2C%20Phuket%2C%20Thailand"
        self.assertEqual(query_from_maps_search_url(url), "Choeng Thale")

    @patch(
        "publisher_social.maps_location.reverse_geocode_district_nominatim",
        return_value="Choeng Thale",
    )
    def test_district_from_coords_url_without_google_key(self, _mock: object) -> None:
        url = "https://www.google.com/maps?q=8.002100,98.307900"
        self.assertEqual(district_from_google_maps_url(url), "Choeng Thale")

    @patch("publisher_social.maps_location.reverse_geocode_district", return_value="Choeng Thale")
    def test_district_from_coords_url_google_fallback(self, _mock: object) -> None:
        url = "https://www.google.com/maps?q=8.002100,98.307900"
        self.assertEqual(district_from_google_maps_url(url), "Choeng Thale")


if __name__ == "__main__":
    unittest.main()
