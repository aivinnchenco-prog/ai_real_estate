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
