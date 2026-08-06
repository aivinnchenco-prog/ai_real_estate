#!/usr/bin/env python3
"""Regression: Instagram video Agent 5 adapter must not pass config as fields."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from publish_pipeline import spawn_agent5_postmypost_ai_agent  # noqa: E402


def test_spawn_agent5_passes_notion_fields_not_config():
    cfg = {
        "postmypost": {"enabled": True},
        "notion": {
            "fields": {
                "object_id": "Объект ID",
            }
        },
    }
    page = {
        "properties": {
            "Объект ID": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "A_20260806_001"}],
            }
        }
    }
    captured: dict = {}

    def fake_queue(**kwargs):
        captured.update(kwargs)
        return {"queued": True}

    with patch("publish_pipeline.postmypost_enabled", return_value=True), patch(
        "publish_pipeline.notion_get_page", return_value=page
    ), patch(
        "publish_pipeline.queue_postmypost_ai_agent",
        fake_queue,
        create=True,
    ), patch.dict(
        sys.modules,
        {
            "agent_5_usher": MagicMock(),
            "agent_5_usher.postmypost_ai_agent": MagicMock(
                queue_postmypost_ai_agent=fake_queue
            ),
        },
    ):
        out = spawn_agent5_postmypost_ai_agent(
            "page-1",
            "instagram",
            cfg,
            post_id="pub-99",
            post_url="https://instagram.com/reel/x",
        )
    assert out == {"queued": True}
    assert captured["object_id"] == "A_20260806_001"
    assert captured["publication_id"] == "pub-99"


def test_object_id_from_page_rejects_publisher_config_as_fields():
    from metricool_post_search import object_id_from_page

    page = {
        "properties": {
            "Объект ID": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "A_20260806_001"}],
            }
        }
    }
    fields = {"object_id": "Объект ID"}
    assert object_id_from_page(page, fields) == "A_20260806_001"

    # publisher.json top-level object_id is a dict — must not pass whole config as fields
    bad_config = {
        "object_id": {"format": "YYYYMMDD_NNN"},
        "notion": {"fields": {"object_id": "Объект ID"}},
    }
    with pytest.raises(TypeError, match="dict"):
        object_id_from_page(page, bad_config)  # type: ignore[arg-type]
