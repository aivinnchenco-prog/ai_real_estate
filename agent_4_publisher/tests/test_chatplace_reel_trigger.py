#!/usr/bin/env python3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from publish_pipeline import spawn_chatplace_if_reel_url_ready  # noqa: E402


def test_spawn_chatplace_skips_without_reel_url():
    cfg = {
        "chatplace": {"enabled": True, "platforms": ["instagram"], "instagram_post_kinds": ["reel"]},
        "notion": {
            "fields": {
                "chatplace_funnel_reel_done": "chatplace_funnel_reel_done",
                "object_id": "Объект ID",
            },
            "published_url_fields": {"instagram_reel": "post_url_instagram_reel"},
        },
    }
    page = {
        "id": "page-1",
        "properties": {
            "chatplace_funnel_reel_done": {"type": "checkbox", "checkbox": False},
            "post_url_instagram_reel": {"type": "url", "url": None},
        },
    }
    with patch("publish_pipeline.notion_get_page", return_value=page):
        assert spawn_chatplace_if_reel_url_ready("page-1", cfg) is None


def test_spawn_chatplace_when_reel_url_ready():
    cfg = {
        "chatplace": {"enabled": True, "platforms": ["instagram"], "instagram_post_kinds": ["reel"]},
        "notion": {
            "fields": {
                "chatplace_funnel_reel_done": "chatplace_funnel_reel_done",
                "object_id": "Объект ID",
            },
            "published_url_fields": {"instagram_reel": "post_url_instagram_reel"},
        },
    }
    page = {
        "id": "page-1",
        "properties": {
            "chatplace_funnel_reel_done": {"type": "checkbox", "checkbox": False},
            "post_url_instagram_reel": {
                "type": "url",
                "url": "https://www.instagram.com/reel/abc123/",
            },
        },
    }
    with patch("publish_pipeline.notion_get_page", return_value=page), patch(
        "publish_pipeline.spawn_deferred_chatplace_funnel",
        return_value={"spawned": True, "post_kind": "reel"},
    ) as spawn:
        out = spawn_chatplace_if_reel_url_ready("page-1", cfg)
        assert out == {"spawned": True, "post_kind": "reel"}
        spawn.assert_called_once()
        assert spawn.call_args.kwargs.get("post_kind") == "reel"
