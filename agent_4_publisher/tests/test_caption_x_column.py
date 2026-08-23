#!/usr/bin/env python3
"""X.com берёт «Описание X.com», остальные соцсети — «Описание соц.сети»."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from metricool_seo import read_base_caption  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "publisher.json").read_text(encoding="utf-8"))


def _page() -> dict:
    return {
        "properties": {
            "Описание соц.сети": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "Длинный тизер для Instagram и TikTok"}],
            },
            "Описание X.com": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "Короткий тизер для X"}],
            },
        }
    }


def _get_prop(page, name, kind):
    prop = (page.get("properties") or {}).get(name) or {}
    if kind == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text") or [])
    return ""


def test_publisher_maps_x_to_caption_x():
    assert CFG["notion"]["fields"]["caption_x"] == "Описание X.com"
    assert CFG["notion"]["caption_by_platform"]["x"] == "caption_x"
    assert CFG["notion"]["caption_by_platform"]["twitter"] == "caption_x"
    assert CFG["notion"]["caption_by_platform"]["instagram"] == "caption_social"


def test_read_base_caption_x_uses_short_column():
    field, text = read_base_caption(_page(), CFG["notion"]["fields"], "x", CFG, _get_prop)
    assert field == "Описание X.com"
    assert text == "Короткий тизер для X"


def test_read_base_caption_instagram_stays_on_social():
    field, text = read_base_caption(
        _page(), CFG["notion"]["fields"], "instagram", CFG, _get_prop
    )
    assert field == "Описание соц.сети"
    assert "Instagram" in text


def test_read_base_caption_x_falls_back_to_social():
    page = {
        "properties": {
            "Описание соц.сети": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "Только соцсеть"}],
            }
        }
    }
    field, text = read_base_caption(page, CFG["notion"]["fields"], "x", CFG, _get_prop)
    assert field == "Описание соц.сети"
    assert text == "Только соцсеть"
