#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from publish_pipeline import metricool_enabled, phone_publisher_enabled, publish_one  # noqa: E402


def test_metricool_disabled_skips_publish_one():
    cfg = {
        "metricool": {"enabled": False},
        "notion": {"fields": {}, "published_url_fields": {}},
    }
    out = publish_one("page-1", "instagram", "2026-07-24T08:00:00Z", True, cfg)
    assert out["skipped"] is True
    assert out["reason"] == "metricool_disabled"


def test_metricool_enabled_by_default():
    cfg = {"metricool": {}}
    assert metricool_enabled(cfg) is True


def test_phone_publisher_flag():
    cfg = {"phone_publisher": {"enabled": True}}
    assert phone_publisher_enabled(cfg) is True
    assert phone_publisher_enabled({}) is False
