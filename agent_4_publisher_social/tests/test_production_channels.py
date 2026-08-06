#!/usr/bin/env python3
"""Production channel policy for phone publisher."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from publisher_social.config import (  # noqa: E402
    production_channels,
    validate_production_channels,
)
from publisher_social.models import CHANNELS  # noqa: E402


class TestPhoneProductionChannels(unittest.TestCase):
    def test_config_allows_only_fb_channels(self) -> None:
        cfg_path = ROOT / "config" / "publisher.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["channels"], ["fb_groups", "fb_marketplace"])
        self.assertEqual(production_channels(cfg), ["fb_groups", "fb_marketplace"])

    def test_models_channels_match_production(self) -> None:
        self.assertEqual(CHANNELS, ("fb_groups", "fb_marketplace"))

    def test_validate_rejects_postmypost_channels(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_production_channels(["tiktok"])
        self.assertIn("tiktok", str(ctx.exception))
        self.assertIn("PostMyPost", str(ctx.exception))

    def test_old_state_channel_keys_not_in_production_queue(self) -> None:
        cfg = {"channels": ["fb_groups", "fb_marketplace", "tiktok"]}
        self.assertEqual(production_channels(cfg), ["fb_groups", "fb_marketplace"])


if __name__ == "__main__":
    unittest.main()
