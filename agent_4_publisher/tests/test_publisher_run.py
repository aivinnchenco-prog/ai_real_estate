#!/usr/bin/env python3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from publisher_run import (  # noqa: E402
    PublishRunContext,
    PublishSlot,
    enumerate_api_slots,
    finalize_batch_status,
    phone_external_slots,
    publish_page_batch,
    release_process_lock,
    try_acquire_process_lock,
)
from publish_pipeline import publish_skip_reason  # noqa: E402

BASE_CFG = {
    "metricool": {"enabled": False},
    "postmypost": {"enabled": True, "project_id": 1},
    "phone_publisher": {"enabled": True, "mode": "fb_only", "channels": ["fb_groups", "fb_marketplace"]},
    "instagram": {"reel_delay_hours": 4},
    "notion": {
        "fields": {
            "agent6_locked": "agent6_locked",
            "agent6_taken_at": "agent6_taken_at",
            "status": "Статус",
            "object_id": "Объект ID",
        },
        "published_url_fields": {
            "instagram_carousel": "post_url_instagram_carousel",
            "instagram_reel": "post_url_instagram_reel",
            "tiktok": "post_url_tiktok",
            "x": "post_url_x",
        },
        "statuses": {"ready": "ready_to_post", "taken": "ready_to_post", "scheduled": "ready_to_post"},
    },
    "carousel": {"enabled": True, "platforms": ["instagram", "tiktok", "x"]},
    "video_format_by_platform": {"instagram": "seedance", "tiktok": "seedance", "x": "seedance"},
}


def _page(*, locked=False, carousel_url=None, reel_url=None, tiktok_url=None, x_url=None):
    props = {
        "agent6_locked": {"type": "checkbox", "checkbox": locked},
        "post_url_instagram_carousel": {"type": "url", "url": carousel_url},
        "post_url_instagram_reel": {"type": "url", "url": reel_url},
        "post_url_tiktok": {"type": "url", "url": tiktok_url},
        "post_url_x": {"type": "url", "url": x_url},
    }
    return {"id": "page-1", "properties": props}


def test_acquire_sets_ownership_and_bypass():
    ctx = PublishRunContext("p1", "run1", dry_run=False, force=False, retry_failed=False)
    fields = BASE_CFG["notion"]["fields"]
    with patch("publisher_run.notion_get_page", return_value=_page()), patch(
        "publisher_run.notion_update_fields"
    ) as nuf:
        reason = try_acquire_process_lock("p1", fields, BASE_CFG, ctx)
    assert reason is None
    assert ctx.owns_lock is True
    assert ctx.bypass_lock is True
    nuf.assert_called_once()
    assert "agent6_locked" in nuf.call_args[0][1]


def test_dry_run_does_not_acquire_lock():
    ctx = PublishRunContext("p1", "run1", dry_run=True, force=False, retry_failed=False)
    with patch("publisher_run.notion_update_fields") as nuf:
        reason = try_acquire_process_lock("p1", BASE_CFG["notion"]["fields"], BASE_CFG, ctx)
    assert reason is None
    assert ctx.owns_lock is False
    nuf.assert_not_called()


def test_external_process_blocked_when_locked():
    ctx = PublishRunContext("p1", "run1", dry_run=False, force=False, retry_failed=False)
    with patch("publisher_run.notion_get_page", return_value=_page(locked=True)):
        reason = try_acquire_process_lock("p1", BASE_CFG["notion"]["fields"], BASE_CFG, ctx)
    assert reason == "agent6_locked"
    assert ctx.owns_lock is False


def test_release_only_when_owner():
    ctx = PublishRunContext("p1", "run1", dry_run=False, force=False, retry_failed=False)
    ctx.owns_lock = True
    fields = BASE_CFG["notion"]["fields"]
    with patch("publisher_run.notion_update_fields") as nuf:
        ok = release_process_lock("p1", fields, ctx)
    assert ok is True
    assert ctx.owns_lock is False
    nuf.assert_called_once_with("p1", {"agent6_locked": {"checkbox": False}})


def test_release_skips_when_not_owner():
    ctx = PublishRunContext("p1", "run1", dry_run=False, force=False, retry_failed=False)
    with patch("publisher_run.notion_update_fields") as nuf:
        ok = release_process_lock("p1", BASE_CFG["notion"]["fields"], ctx)
    assert ok is False
    nuf.assert_not_called()


def test_instagram_slots_are_separate():
    slots = enumerate_api_slots(["instagram"], BASE_CFG, post_mode=None, scheduled_time="2026-08-06T10:00:00.000Z")
    keys = [s.slot_key for s in slots]
    assert keys == ["instagram:carousel", "instagram:video"]


def test_phone_external_slots():
    slots = phone_external_slots(BASE_CFG)
    assert [s.slot_key for s in slots] == ["facebook:groups", "facebook:marketplace"]
    assert all(s.external for s in slots)


def test_first_slot_failure_second_still_runs():
    calls: list[str] = []

    def fake_publish(page_id, platform, scheduled_time, dry_run, config, **kwargs):
        calls.append(f"{platform}:{kwargs.get('mode')}")
        if platform == "instagram" and kwargs.get("mode") == "video":
            raise RuntimeError("instagram video failed")
        return {
            "page_id": page_id,
            "platform": platform,
            "mode": kwargs.get("mode"),
            "skipped": False,
            "metricool_post_id": "1",
        }

    with patch("publisher_run.try_acquire_process_lock", return_value=None), patch(
        "publisher_run.release_process_lock", return_value=True
    ), patch("publisher_run.publish_one", side_effect=fake_publish):
        batch = publish_page_batch(
            "page-1",
            ["instagram", "tiktok"],
            BASE_CFG,
            dry_run=False,
            post_mode=None,
        )
    assert "instagram:carousel" in batch.published or "instagram:carousel" in batch.skipped
    assert "instagram:video" in batch.failed
    assert any(c.startswith("tiktok:") for c in calls)
    assert batch.lock_released is True
    assert batch.status in ("partial_success", "failed")


def test_lock_released_after_total_failure():
    def boom(*args, **kwargs):
        raise RuntimeError("all fail")

    with patch("publisher_run.try_acquire_process_lock", return_value=None), patch(
        "publisher_run.release_process_lock", return_value=True
    ) as rel, patch("publisher_run.publish_one", side_effect=boom):
        batch = publish_page_batch("page-1", ["tiktok"], BASE_CFG, dry_run=False)
    rel.assert_called_once()
    assert batch.lock_released is True
    assert batch.status == "failed"


def test_awaiting_phone_when_only_external_pending():
    batch = finalize_batch_status(
        type(
            "R",
            (),
            {
                "published": ["instagram:carousel"],
                "failed": [],
                "skipped": [],
                "pending_external": ["facebook:groups", "facebook:marketplace"],
            },
        )()
    )
    assert batch == "awaiting_phone"


def test_instagram_video_independent_from_carousel_skip():
    page = _page(carousel_url="https://instagram.com/p/c", reel_url=None)
    carousel_reason = publish_skip_reason(
        page,
        BASE_CFG["notion"]["fields"],
        "instagram",
        BASE_CFG,
        upload_video=False,
        mode="carousel",
        bypass_lock=True,
        force=False,
    )
    video_reason = publish_skip_reason(
        page,
        BASE_CFG["notion"]["fields"],
        "instagram",
        BASE_CFG,
        upload_video=True,
        mode="video",
        bypass_lock=True,
        force=False,
    )
    assert carousel_reason and "already published" in carousel_reason
    assert video_reason is None


def test_force_does_not_skip_published_reel():
    page = _page(reel_url="https://instagram.com/reel/x")
    reason = publish_skip_reason(
        page,
        BASE_CFG["notion"]["fields"],
        "instagram",
        BASE_CFG,
        upload_video=True,
        mode="video",
        bypass_lock=True,
        force=True,
    )
    assert reason and "already published" in reason
