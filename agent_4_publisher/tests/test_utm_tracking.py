#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from utm_tracking import (  # noqa: E402
    append_utm_to_url,
    append_utm_to_urls_in_text,
    extract_utm_campaign,
    utm_params,
)


def test_utm_params():
    cfg = {"postmypost": {"utm": {"source_template": "{platform}", "medium": "social"}}}
    assert utm_params("instagram", "20260701_001", cfg) == {
        "utm_source": "instagram",
        "utm_medium": "social",
        "utm_campaign": "20260701_001",
    }


def test_append_utm_to_url():
    cfg = {"postmypost": {"utm": {}}}
    url = append_utm_to_url(
        "https://t.me/OpenHome_th/42",
        "instagram",
        "A_20260713_003",
        cfg,
    )
    assert "utm_campaign=A_20260713_003" in url
    assert "utm_source=instagram" in url


def test_append_utm_to_urls_in_text():
    text = "Смотрите https://t.me/OpenHome_th/42 и сайт"
    out = append_utm_to_urls_in_text(text, "instagram", "20260701_001", {})
    assert "utm_campaign=20260701_001" in out


def test_extract_utm_campaign():
    link = "https://t.me/c/123/45?utm_source=instagram&utm_campaign=20260708_001"
    assert extract_utm_campaign(link) == "20260708_001"
    assert extract_utm_campaign("no utm") is None
