#!/usr/bin/env python3
"""Unit: Airbnb location fallback from node.pdpPresentation.location."""

from __future__ import annotations

import os
import unittest

from airbnb_parser import AirbnbParser


class LocationFallbackTest(unittest.TestCase):
    def test_fallback_from_pdp_presentation(self):
        parser = AirbnbParser(headless=True)
        data = {
            "niobeClientData": [
                [
                    None,
                    {
                        "data": {
                            "presentation": {
                                "stayProductDetailPage": {
                                    "sections": {
                                        "sections": [
                                            {
                                                "sectionId": "LOCATION_DEFAULT",
                                                "section": {"__typename": "Stub"},
                                            }
                                        ]
                                    }
                                }
                            },
                            "node": {
                                "pdpPresentation": {
                                    "overview": {"title": "Villa", "items": []},
                                    "amenities": {"seeAllAmenitiesGroups": []},
                                    "personCapacity": 4,
                                    "location": {
                                        "latitude": 8.00567,
                                        "longitude": 98.34456,
                                        "subtitle": "Си Сунтон, Таиланд",
                                    },
                                },
                                "location": {
                                    "coordinate": {
                                        "latitude": 8.00567,
                                        "longitude": 98.34456,
                                    }
                                },
                            },
                        }
                    },
                ]
            ]
        }
        # amenities path expects list of groups with title/amenities — stub empty via patch
        details = {
            "Название": "",
            "Локация": parser._get_location({"section": {"__typename": "Stub"}}),
        }
        self.assertEqual(details["Локация"], {})
        fb = parser._extract_location_fallback(data)
        self.assertAlmostEqual(fb["latitude"], 8.00567, places=4)
        self.assertAlmostEqual(fb["longitude"], 98.34456, places=4)
        self.assertIn("Сунтон", fb["subtitle"])


if __name__ == "__main__":
    unittest.main()
