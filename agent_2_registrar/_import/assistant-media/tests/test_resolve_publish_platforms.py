#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from chain_runner import phone_publisher_live, resolve_publish_platforms  # noqa: E402


class ResolvePublishPlatformsTests(unittest.TestCase):
    def test_postmypost_on_metricool_off_returns_platforms(self):
        chain = {"publish_platforms": ["instagram"]}
        plats = resolve_publish_platforms(chain)
        self.assertTrue(plats)
        self.assertIn("instagram", plats)

    def test_both_off_falls_back_to_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            estate = Path(tmp)
            pub_dir = estate / "agent_4_publisher" / "config"
            pub_dir.mkdir(parents=True)
            (pub_dir / "publisher.json").write_text(
                json.dumps(
                    {
                        "metricool": {"enabled": False},
                        "postmypost": {"enabled": False},
                        "publish_platforms": ["tiktok"],
                    }
                ),
                encoding="utf-8",
            )
            fake_root = estate / "agent_2_registrar" / "_import" / "assistant-media"
            fake_root.mkdir(parents=True)
            with patch("chain_runner.ROOT", fake_root):
                plats = resolve_publish_platforms({"publish_platforms": ["instagram"]})
            self.assertEqual(plats, ["instagram"])

    def test_postmypost_on_via_mocked_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            estate = Path(tmp)
            pub_dir = estate / "agent_4_publisher" / "config"
            pub_dir.mkdir(parents=True)
            (pub_dir / "publisher.json").write_text(
                json.dumps(
                    {
                        "metricool": {"enabled": False},
                        "postmypost": {"enabled": True},
                        "publish_platforms": ["instagram", "tiktok", "x"],
                    }
                ),
                encoding="utf-8",
            )
            fake_root = estate / "agent_2_registrar" / "_import" / "assistant-media"
            fake_root.mkdir(parents=True)
            with patch("chain_runner.ROOT", fake_root):
                plats = resolve_publish_platforms({})
            self.assertEqual(plats, ["instagram", "tiktok", "x"])


class PhonePublisherLiveTests(unittest.TestCase):
    def test_chain_runner_live_from_publisher_json(self):
        chain = {"phone_publisher_live": False}
        pub = {"phone_publisher": {"runner": "chain", "live": True}}
        self.assertTrue(phone_publisher_live(chain, pub))

    def test_termux_never_live_from_chain(self):
        chain = {"phone_publisher_live": True}
        pub = {"phone_publisher": {"runner": "termux", "live": True}}
        self.assertFalse(phone_publisher_live(chain, pub))

    def test_chain_fallback_to_pipeline_flag(self):
        chain = {"phone_publisher_live": True}
        pub = {"phone_publisher": {"runner": "chain"}}
        self.assertTrue(phone_publisher_live(chain, pub))


if __name__ == "__main__":
    unittest.main()
