#!/usr/bin/env python3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from postmypost_seo import (  # noqa: E402
    ai_adapt_caption,
    append_geolocation_line,
    comment_cta_enabled,
    resolve_comment_cta,
)
from setup_postmypost_funnel import (  # noqa: E402
    automation_enabled,
    build_dm_message,
    should_run_postmypost_automation,
)


def test_automation_enabled():
    cfg = {"postmypost": {"enabled": True, "automation": {"enabled": True}}}
    assert automation_enabled(cfg) is True
    cfg["postmypost"]["automation"]["enabled"] = False
    assert automation_enabled(cfg) is False


def test_should_run_postmypost_automation_reel():
    cfg = {
        "postmypost": {
            "enabled": True,
            "automation": {"enabled": True, "instagram_post_kinds": ["reel"]},
        }
    }
    assert should_run_postmypost_automation("instagram", cfg, upload_video=True, mode="video")
    assert not should_run_postmypost_automation("instagram", cfg, mode="carousel")


def test_build_dm_message_utm():
    msg = build_dm_message(
        "Объект {object_id}: {telegram_url}",
        "https://t.me/x/1?utm_campaign=20260701_001",
        "20260701_001",
    )
    assert "20260701_001" in msg
    assert "utm_campaign" in msg


def test_ai_adapt_x_truncates():
    cfg = {"postmypost": {"ai_adapt": {"enabled": True}}}
    long = "line1\n" * 50
    out = ai_adapt_caption(long, "x", cfg)
    assert len(out) <= 280


def test_append_geolocation_line():
    loc = {"name": "Bang Tao Beach, Phuket"}
    out = append_geolocation_line("Caption", loc, enabled=True)
    assert "Bang Tao Beach" in out


def test_comment_cta_default():
    cfg = {
        "postmypost": {
            "comment_cta": {"enabled": True},
            "reply_agent": {
                "enabled": True,
                "caption_cta_template": "Код {object_id} → {telegram_channel}",
                "manager_telegram": "@OpenHome_th",
            },
        },
        "telegram": {"channel": "@OpenHome_th"},
        "notion": {"fields": {}},
    }
    assert comment_cta_enabled(cfg)
    cta = resolve_comment_cta(
        "tiktok", {"properties": {}}, {}, cfg, lambda *a: "", object_id="20260701_001"
    )
    assert "20260701_001" in cta


def test_comment_cta_by_platform():
    cfg = {
        "postmypost": {
            "comment_cta": {"enabled": True, "by_platform": {"youtube": "YT {object_id}"}},
            "reply_agent": {"enabled": True},
        },
    }
    cta = resolve_comment_cta(
        "youtube", {"properties": {}}, {}, cfg, lambda *a: "", object_id="X"
    )
    assert cta == "YT X"


def test_threads_cta_is_tg_only_without_object_code():
    cfg = {
        "postmypost": {
            "comment_cta": {"enabled": True},
            "reply_agent": {
                "enabled": True,
                "manager_telegram": "@OpenHome_th",
                "by_platform": {"threads": "TG {telegram_channel}"},
            },
        },
    }
    cta = resolve_comment_cta(
        "threads", {"properties": {}}, {}, cfg, lambda *a: "", object_id="A_20260810_003"
    )
    assert cta == "TG @OpenHome_th"
    assert "A_20260810_003" not in cta


def test_strip_inline_object_tag():
    from postmypost_seo import strip_inline_object_tag

    raw = (
        "Вилла в Аренду, Пхукет! Бронь в WhatsApp +66625124002 "
        "Объект №A_20260810_003"
    )
    out = strip_inline_object_tag(raw, "A_20260810_003")
    assert "Объект №" not in out
    assert "A_20260810_003" not in out
    assert "WhatsApp +66625124002" in out


def test_threads_caption_bundle_no_triple_object_code():
    from postmypost_seo import build_postmypost_caption_bundle

    cfg = {
        "postmypost": {
            "enabled": True,
            "auto_geolocation": False,
            "ai_adapt": {"enabled": True},
            "comment_cta": {"enabled": True, "append_to_caption": True},
            "reply_agent": {
                "enabled": True,
                "manager_telegram": "@OpenHome_th",
                "object_code_line_template": "🏷 Код объекта: {object_id}",
                "by_platform": {"threads": "TG {telegram_channel}"},
            },
            "seo": {"enabled": True},
        },
        "metricool": {"seo": {"enabled": True, "hashtag_randomize": {"enabled": False}}},
        "notion": {
            "fields": {"caption_social": "Описание соц.сети"},
            "caption_by_platform": {"threads": "caption_social"},
        },
        "telegram": {"channel": "@OpenHome_th"},
    }
    page = {
        "properties": {
            "Описание соц.сети": {
                "type": "rich_text",
                "rich_text": [
                    {
                        "plain_text": (
                            "Вилла в Аренду, Пхукет! 3 спальни, бассейн. "
                            "Бронь в WhatsApp +66625124002 Объект №A_20260810_003"
                        )
                    }
                ],
            }
        }
    }

    def get_prop(pg, name, kind):
        prop = (pg.get("properties") or {}).get(name) or {}
        if kind == "rich_text":
            return "".join(t.get("plain_text", "") for t in prop.get("rich_text") or [])
        return ""

    with patch("postmypost_seo.build_hashtags", return_value="TripHomePhuket #Phuket #A_20260810_003"):
        bundle = build_postmypost_caption_bundle(
            page,
            cfg["notion"]["fields"],
            "threads",
            cfg,
            get_prop,
            lambda q: [],
            object_id="A_20260810_003",
        )
    text = bundle["text"]
    assert "Объект №" not in text
    assert "🏷 Код объекта: A_20260810_003" in text
    assert "TG @OpenHome_th" in text
    assert "Код A_20260810_003 →" not in text
    assert "WhatsApp +66625124002" in text
    # Object id only in badge line (and optional hashtag block), never thrice.
    assert text.count("A_20260810_003") <= 2
    assert text.split("🏷 Код объекта:")[0].count("A_20260810_003") == 0
    combined = "\n".join(
        [
            text,
            bundle.get("firstCommentText") or "",
            bundle.get("hashtags") or "",
        ]
    )
    assert "#A_20260810_003" in combined or "TripHomePhuket" in combined
