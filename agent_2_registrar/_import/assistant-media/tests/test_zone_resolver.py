#!/usr/bin/env python3
"""resolve_zone: Район → Notion Select «Зона» (без сети)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zone_resolver import (  # noqa: E402
    apply_zone_to_properties,
    resolve_zone,
    should_write_zone,
)


class ResolveZoneTests(unittest.TestCase):
    def test_requested_cases(self):
        self.assertEqual(resolve_zone("Choeng Thale"), "Bang Tao")
        self.assertEqual(resolve_zone("Nai Harn"), "Nai Harn")
        self.assertEqual(resolve_zone("Thep Krasattri"), "Thalang")
        self.assertIsNone(resolve_zone("Kathu"))
        self.assertIsNone(resolve_zone("Phuket, Thailand"))
        self.assertEqual(resolve_zone("Kata"), "Karon")
        self.assertEqual(resolve_zone("Mai Khao"), "Nai Thon")

    def test_extra_aliases(self):
        self.assertEqual(resolve_zone("Si Sunthon"), "Bang Tao")
        self.assertEqual(resolve_zone("Laguna"), "Bang Tao")
        self.assertEqual(resolve_zone("Layan"), "Bang Tao")
        self.assertEqual(resolve_zone("Cherng Talay"), "Bang Tao")
        self.assertEqual(resolve_zone("  choeng   thale "), "Bang Tao")
        self.assertIsNone(resolve_zone("Phuket"))
        self.assertIsNone(resolve_zone(""))
        self.assertIsNone(resolve_zone("Cape Panwa"))

    def test_write_policy(self):
        self.assertEqual(
            should_write_zone(zone="Nai Harn", is_new_page=True),
            "set",
        )
        self.assertEqual(
            should_write_zone(zone=None, is_new_page=True),
            "omit",
        )
        self.assertEqual(
            should_write_zone(
                zone="Bang Tao",
                is_new_page=False,
                old_district="Choeng Thale",
                new_district="Choeng Thale",
                old_zone="Bang Tao",
            ),
            "omit",
        )
        self.assertEqual(
            should_write_zone(
                zone="Nai Harn",
                is_new_page=False,
                old_district="Nai Harn",
                new_district="Nai Harn",
                old_zone=None,
            ),
            "set",
        )
        self.assertEqual(
            should_write_zone(
                zone=None,
                is_new_page=False,
                old_district="Rawai",
                new_district="Kathu",
                old_zone="Rawai",
            ),
            "clear",
        )

    def test_apply_sets_select(self):
        props: dict = {}
        apply_zone_to_properties(
            props,
            field_name="Зона",
            zone="Bang Tao",
            is_new_page=True,
            new_district="Choeng Thale",
        )
        self.assertEqual(props["Зона"], {"select": {"name": "Bang Tao"}})


if __name__ == "__main__":
    unittest.main()
