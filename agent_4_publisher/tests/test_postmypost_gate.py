#!/usr/bin/env python3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from publish_pipeline import postmypost_enabled, publish_one, social_publisher_backend  # noqa: E402


def test_postmypost_backend_selected():
    cfg = {"metricool": {"enabled": False}, "postmypost": {"enabled": True}}
    assert social_publisher_backend(cfg) == "postmypost"


def test_postmypost_enabled_flag():
    cfg = {"postmypost": {"enabled": True}}
    assert postmypost_enabled(cfg) is True
    assert postmypost_enabled({}) is False


def test_postmypost_publish_one_dry_run():
    cfg = {
        "metricool": {"enabled": False},
        "postmypost": {"enabled": True, "project_id": 355063},
        "notion": {
            "fields": {
                "status": "Статус",
                "photo": "Фото",
                "agent6_locked": "agent6_locked",
                "caption_social": "Описание соц.сети",
            },
            "published_url_fields": {"instagram": "post_url_instagram_carousel"},
            "statuses": {"ready": "ready_to_post", "taken": "ready_to_post"},
            "caption_by_platform": {"instagram": "caption_social"},
        },
        "carousel": {
            "enabled": True,
            "platforms": ["instagram"],
            "min_images_by_platform": {"instagram": 2},
        },
        "video_format_by_platform": {"instagram": "seedance"},
    }
    page = {
        "properties": {
            "Статус": {"status": {"name": "ready_to_post"}},
            "Фото": {"url": "https://example.com/gallery"},
            "agent6_locked": {"checkbox": False},
            "Описание соц.сети": {
                "rich_text": [{"plain_text": "Test caption"}],
            },
        },
    }

    with patch("publish_pipeline.notion_get_page", return_value=page), patch(
        "publish_pipeline.designed_carousel_urls",
        return_value=["https://example.com/slide1.jpg", "https://example.com/slide2.jpg"],
    ), patch("publish_pipeline.publish_skip_reason", return_value=None), patch(
        "daily_quota.find_free_day", return_value=(0, "ok")
    ), patch(
        "publish_pipeline.build_caption_bundle",
        return_value={"text": "Test caption", "firstCommentText": "", "hashtags": ""},
    ):
        out = publish_one(
            "page-1",
            "instagram",
            "2026-07-24T08:00:00Z",
            True,
            cfg,
            mode="carousel",
        )
    assert out.get("dry_run") is True
    assert out.get("skipped") is not True
