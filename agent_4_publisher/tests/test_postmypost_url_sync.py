#!/usr/bin/env python3
"""Tests for PostMyPost social URL extraction and Notion sync."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent_6_qualifier" / "src"))

from postmypost_client import extract_publication_url  # noqa: E402
from postmypost_publication_state import (  # noqa: E402
    lookup_publication,
    postmypost_slot_id,
    register_publication,
)
from postmypost_social_url import (  # noqa: E402
    decide_notion_url_update,
    is_postmypost_planner_url,
    is_valid_social_post_url,
)
from sync_postmypost_urls import sync_postmypost_url_to_notion  # noqa: E402
from agent6_qualifier.publication_mapping_store import (  # noqa: E402
    InMemoryPublicationMappingStore,
    PublicationMapping,
)
from agent6_qualifier.publication_resolver import resolve_publication_reference  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "postmypost"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def pmp_config() -> dict:
    return {
        "postmypost": {
            "enabled": True,
            "project_id": 355063,
            "platform_accounts": {
                "instagram": [2214120],
                "tiktok": [2214123],
                "x": [2214180],
                "twitter": [2214180],
                "linkedin": [2214176],
                "facebook": [2214118],
                "youtube": [2214116],
                "threads": [2214188],
            },
        },
        "notion": {
            "published_url_fields": {
                "instagram_carousel": "post_url_instagram_carousel",
                "instagram_reel": "post_url_instagram_reel",
                "tiktok": "post_url_tiktok",
                "tiktok_carousel": "post_url_tiktok_carousel",
                "x": "post_url_x",
                "linkedin": "post_url_linkedin",
                "facebook": "post_url_facebook",
                "youtube": "post_url_youtube",
                "threads": "post_url_threads",
            },
        },
    }


def test_instagram_published_extracts_posts_url(pmp_config) -> None:
    payload = _fixture("instagram_published.json")
    url = extract_publication_url(payload, "instagram", pmp_config)
    assert url == "https://www.instagram.com/p/DbudjpLil5I"


def test_instagram_scheduled_returns_none(pmp_config) -> None:
    payload = _fixture("instagram_scheduled.json")
    assert extract_publication_url(payload, "instagram", pmp_config) is None


def test_details_link_not_used(pmp_config) -> None:
    payload = _fixture("instagram_published.json")
    assert payload["details"][0]["link"].startswith("https://example.com")
    url = extract_publication_url(payload, "instagram", pmp_config)
    assert "example.com" not in (url or "")


def test_multi_account_picks_correct_platform(pmp_config) -> None:
    payload = _fixture("multi_account_published.json")
    ig = extract_publication_url(payload, "instagram", pmp_config)
    tt = extract_publication_url(payload, "tiktok", pmp_config)
    assert ig == "https://www.instagram.com/p/MultiAcctIG"
    assert tt == "https://www.tiktok.com/@openhome/video/999"


@pytest.mark.parametrize(
    "fixture,platform,expected",
    [
        ("tiktok_published.json", "tiktok", "https://www.tiktok.com/@openhome/video/7123456789"),
        ("x_published.json", "x", "https://twitter.com/OpenHomeTh/status/2085589089055605175"),
        ("linkedin_published.json", "linkedin", "https://www.linkedin.com/feed/update/urn:li:ugcPost:7491355098456129536"),
        ("youtube_published.json", "youtube", "https://www.youtube.com/shorts/IlNO8zYl7qY"),
        ("threads_published.json", "threads", "https://www.threads.com/open.home.th/post/Dbuek8xjZNV"),
        ("facebook_published.json", "facebook", "https://www.facebook.com/122105052381405477"),
    ],
)
def test_platform_urls(fixture, platform, expected, pmp_config) -> None:
    url = extract_publication_url(_fixture(fixture), platform, pmp_config)
    assert url == expected
    network = "twitter" if platform == "x" else platform
    assert is_valid_social_post_url(url, network)


def test_planner_url_rejected() -> None:
    planner = "https://app.postmypost.io/publications/31462701"
    assert is_postmypost_planner_url(planner)
    assert not is_valid_social_post_url(planner, "instagram")


def test_facebook_numeric_url_valid() -> None:
    url = "https://www.facebook.com/122105052381405477"
    assert is_valid_social_post_url(url, "facebook")


def test_instagram_slots_differ() -> None:
    assert postmypost_slot_id("instagram", post_kind="carousel") == "instagram:carousel"
    assert postmypost_slot_id("instagram", post_kind="reel") == "instagram:reel"
    assert postmypost_slot_id("tiktok", post_kind="carousel") == "tiktok:carousel"
    assert postmypost_slot_id("tiktok", upload_video=True) == "tiktok:video"


def test_tiktok_carousel_published_url_field(pmp_config) -> None:
    from publish_pipeline import published_url_field

    assert published_url_field("tiktok", pmp_config, mode="carousel") == "post_url_tiktok_carousel"
    assert published_url_field("tiktok", pmp_config, upload_video=True) == "post_url_tiktok"


def test_tiktok_carousel_sync_writes_correct_field(pmp_config) -> None:
    page = {
        "properties": {
            "post_url_tiktok_carousel": {
                "type": "url",
                "url": "https://app.postmypost.io/publications/99",
            },
        }
    }
    payload = _fixture("tiktok_published.json")
    updates: dict = {}

    def fake_update(page_id, fields):
        updates.update(fields)

    with patch("sync_postmypost_urls.postmypost_get_publication", return_value=payload), patch(
        "sync_postmypost_urls.notion_update_fields", side_effect=fake_update
    ):
        result = sync_postmypost_url_to_notion(
            "page-1",
            "tiktok",
            pmp_config,
            publication_id="31462708",
            post_kind="carousel",
            page=page,
        )
    assert result["url_field"] == "post_url_tiktok_carousel"
    assert updates["post_url_tiktok_carousel"]["url"] == _fixture("tiktok_published.json")["posts"][0]["url"]
    assert "post_url_tiktok" not in updates


def test_decide_notion_url_update_cases() -> None:
    ig = "https://www.instagram.com/p/ABC"
    planner = "https://app.postmypost.io/publications/1"
    other = "https://www.instagram.com/p/OTHER"

    assert decide_notion_url_update("", ig, "instagram") == (True, ig, "write_new")
    assert decide_notion_url_update(planner, ig, "instagram") == (True, ig, "replace_planner")
    assert decide_notion_url_update(ig, ig, "instagram") == (False, None, "noop_same")
    assert decide_notion_url_update(other, ig, "instagram") == (False, None, "conflict")
    assert decide_notion_url_update("", None, "instagram") == (False, None, "not_ready")
    # Planner alone is not a valid "new" live URL (deferred sync must wait).
    assert decide_notion_url_update("", planner, "instagram") == (False, None, "invalid_new")


def test_resolve_url_to_save_after_schedule_prefers_live_then_planner() -> None:
    from publish_pipeline import resolve_url_to_save_after_schedule

    planner = "https://app.postmypost.io/publications/31505558"
    live = "https://www.instagram.com/p/ABC"
    assert resolve_url_to_save_after_schedule(published_url=None, planner_url=planner) == planner
    assert resolve_url_to_save_after_schedule(published_url=live, planner_url=planner) == live
    assert resolve_url_to_save_after_schedule(published_url=None, planner_url=None) is None


def test_publication_state_per_slot(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    register_publication(
        page_id="page-1",
        object_id="A_1",
        platform="instagram",
        publication_id="111",
        planner_url="https://app.postmypost.io/publications/111",
        scheduled_time="2026-08-07T10:00:00Z",
        post_kind="carousel",
        path=state_file,
    )
    register_publication(
        page_id="page-1",
        object_id="A_1",
        platform="instagram",
        publication_id="222",
        planner_url="https://app.postmypost.io/publications/222",
        scheduled_time="2026-08-07T14:00:00Z",
        post_kind="reel",
        path=state_file,
    )
    carousel = lookup_publication("page-1", "instagram", post_kind="carousel", path=state_file)
    reel = lookup_publication("page-1", "instagram", post_kind="reel", path=state_file)
    assert carousel["publication_id"] == "111"
    assert reel["publication_id"] == "222"


def test_sync_writes_correct_notion_field(pmp_config) -> None:
    page = {
        "properties": {
            "post_url_instagram_carousel": {"type": "url", "url": "https://app.postmypost.io/publications/1"},
        }
    }
    payload = _fixture("instagram_published.json")
    updates: dict = {}

    def fake_update(page_id, fields):
        updates.update(fields)

    with patch("sync_postmypost_urls.postmypost_get_publication", return_value=payload), patch(
        "sync_postmypost_urls.notion_update_fields", side_effect=fake_update
    ):
        result = sync_postmypost_url_to_notion(
            "page-1",
            "instagram",
            pmp_config,
            publication_id="31462701",
            post_kind="carousel",
            page=page,
        )
    assert result["updated"] is True
    assert result["reason"] == "replace_planner"
    assert result["url_field"] == "post_url_instagram_carousel"
    assert updates["post_url_instagram_carousel"]["url"] == "https://www.instagram.com/p/DbudjpLil5I"
    assert "post_url_instagram_reel" not in updates


def test_instagram_carousel_and_reel_sync_to_separate_notion_fields(pmp_config) -> None:
    """Different slots (publication IDs) must land in different post_url_* columns."""
    carousel_pub = "31462701"
    reel_pub = "31462704"
    carousel_payload = _fixture("instagram_published.json")
    reel_payload = _fixture("instagram_reel_published.json")

    page = {
        "properties": {
            "post_url_instagram_carousel": {
                "type": "url",
                "url": f"https://app.postmypost.io/publications/{carousel_pub}",
            },
            "post_url_instagram_reel": {
                "type": "url",
                "url": f"https://app.postmypost.io/publications/{reel_pub}",
            },
        }
    }
    updates: dict = {}

    def fake_update(page_id, fields):
        updates.update(fields)

    def fake_get_publication(publication_id, config):
        if str(publication_id) == carousel_pub:
            return carousel_payload
        if str(publication_id) == reel_pub:
            return reel_payload
        raise AssertionError(f"unexpected publication_id: {publication_id}")

    with patch("sync_postmypost_urls.postmypost_get_publication", side_effect=fake_get_publication), patch(
        "sync_postmypost_urls.notion_update_fields", side_effect=fake_update
    ):
        carousel_result = sync_postmypost_url_to_notion(
            "page-1",
            "instagram",
            pmp_config,
            publication_id=carousel_pub,
            post_kind="carousel",
            page=page,
        )
        reel_result = sync_postmypost_url_to_notion(
            "page-1",
            "instagram",
            pmp_config,
            publication_id=reel_pub,
            post_kind="reel",
            page=page,
        )

    assert carousel_result["url_field"] == "post_url_instagram_carousel"
    assert reel_result["url_field"] == "post_url_instagram_reel"
    assert carousel_result["updated"] is True
    assert reel_result["updated"] is True
    assert updates["post_url_instagram_carousel"]["url"] == "https://www.instagram.com/p/DbudjpLil5I"
    assert updates["post_url_instagram_reel"]["url"] == "https://www.instagram.com/reel/DreelSlotTest"
    assert updates["post_url_instagram_carousel"]["url"] != updates["post_url_instagram_reel"]["url"]


def test_agent6_resolver_finds_listing_by_real_social_url() -> None:
    store = InMemoryPublicationMappingStore(
        [
            PublicationMapping(
                listing_id="A_20260807_001",
                page_id="page-1",
                platform="instagram_carousel",
                publication_id=None,
                canonical_url="https://instagram.com/p/DbudjpLil5I",
                notion_url="https://www.instagram.com/p/DbudjpLil5I",
            )
        ]
    )
    out = resolve_publication_reference(
        "https://www.instagram.com/p/DbudjpLil5I/",
        None,
        None,
        None,
        None,
        store=store,
    )
    assert out.found is True
    assert out.listing_id == "A_20260807_001"
