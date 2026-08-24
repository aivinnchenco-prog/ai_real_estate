#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from publish_pipeline import publish_skip_reason  # noqa: E402

CFG = {
    "notion": {
        "published_url_fields": {
            "instagram_carousel": "post_url_instagram_carousel",
            "instagram_reel": "post_url_instagram_reel",
            "x": "post_url_x",
        },
        "fields": {
            "agent6_locked": "agent6_locked",
            "status": "Статус",
        },
    }
}


def _page(*, carousel_url=None, reel_url=None, locked=False):
    props = {
        "agent6_locked": {"type": "checkbox", "checkbox": locked},
        "post_url_instagram_carousel": {
            "type": "url",
            "url": carousel_url,
        },
        "post_url_instagram_reel": {
            "type": "url",
            "url": reel_url,
        },
    }
    return {"id": "page-1", "properties": props}


def test_skips_published_carousel_even_with_bypass_lock():
    page = _page(carousel_url="https://instagram.com/p/abc")
    reason = publish_skip_reason(
        page,
        CFG["notion"]["fields"],
        "instagram",
        CFG,
        upload_video=False,
        mode="carousel",
        bypass_lock=True,
        force=True,
    )
    assert reason and "already published" in reason


def test_skips_published_reel_when_force_true():
    page = _page(reel_url="https://instagram.com/reel/xyz")
    reason = publish_skip_reason(
        page,
        CFG["notion"]["fields"],
        "instagram",
        CFG,
        upload_video=True,
        mode="video",
        bypass_lock=True,
        force=True,
    )
    assert reason and "post_url_instagram_reel" in reason


def test_blocks_locked_without_bypass():
    page = _page(locked=True)
    reason = publish_skip_reason(
        page,
        CFG["notion"]["fields"],
        "instagram",
        CFG,
        upload_video=False,
        mode="carousel",
        bypass_lock=False,
        force=False,
    )
    assert reason == "agent6_locked"


def test_allows_carousel_when_only_reel_url_filled():
    page = _page(reel_url="https://instagram.com/reel/xyz")
    reason = publish_skip_reason(
        page,
        CFG["notion"]["fields"],
        "instagram",
        CFG,
        upload_video=False,
        mode="carousel",
        bypass_lock=True,
        force=True,
    )
    assert reason is None


TIKTOK_CFG = {
    "notion": {
        "published_url_fields": {
            "tiktok": "post_url_tiktok",
            "tiktok_carousel": "post_url_tiktok_carousel",
        },
        "fields": {
            "agent6_locked": "agent6_locked",
            "status": "Статус",
        },
    }
}


def _tiktok_page(*, video_url=None, carousel_url=None):
    props = {
        "agent6_locked": {"type": "checkbox", "checkbox": False},
        "post_url_tiktok": {"type": "url", "url": video_url},
        "post_url_tiktok_carousel": {"type": "url", "url": carousel_url},
    }
    return {"id": "page-tk", "properties": props}


def test_tiktok_carousel_not_skipped_when_video_url_present():
    page = _tiktok_page(
        video_url="https://app.postmypost.io/uk/p/B4RHA#share=31672358"
    )
    reason = publish_skip_reason(
        page,
        TIKTOK_CFG["notion"]["fields"],
        "tiktok",
        TIKTOK_CFG,
        upload_video=False,
        mode="carousel",
        bypass_lock=True,
        force=True,
    )
    assert reason is None


def test_tiktok_carousel_skipped_when_carousel_url_present():
    page = _tiktok_page(
        carousel_url="https://app.postmypost.io/uk/p/B4RHA#share=999"
    )
    reason = publish_skip_reason(
        page,
        TIKTOK_CFG["notion"]["fields"],
        "tiktok",
        TIKTOK_CFG,
        upload_video=False,
        mode="carousel",
        bypass_lock=True,
        force=True,
    )
    assert reason and "post_url_tiktok_carousel" in reason


def test_skips_when_local_registry_has_slot(tmp_path, monkeypatch):
    from postmypost_publication_state import register_publication, state_path

    page = _page()
    page["id"] = "page-local-1"
    state_file = tmp_path / "postmypost_publications.json"
    monkeypatch.setattr(
        "postmypost_publication_state.state_path", lambda: state_file
    )
    register_publication(
        page_id="page-local-1",
        object_id="A_1",
        platform="instagram",
        publication_id="31505570",
        planner_url="https://app.postmypost.io/publications/31505570",
        scheduled_time="2026-08-10T02:55:10.000Z",
        post_kind="carousel",
        upload_video=False,
        path=state_file,
    )
    reason = publish_skip_reason(
        page,
        CFG["notion"]["fields"],
        "instagram",
        CFG,
        upload_video=False,
        mode="carousel",
        bypass_lock=True,
        force=True,
    )
    assert reason and "already published (local:" in reason


def test_resolve_url_to_save_prefers_live_then_planner():
    from publish_pipeline import resolve_url_to_save_after_schedule

    live = "https://www.instagram.com/p/ABC"
    planner = "https://app.postmypost.io/publications/1"
    assert resolve_url_to_save_after_schedule(published_url=live, planner_url=planner) == live
    assert resolve_url_to_save_after_schedule(published_url=None, planner_url=planner) == planner
    assert resolve_url_to_save_after_schedule(published_url=None, planner_url=None) is None
