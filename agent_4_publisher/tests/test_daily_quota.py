#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from daily_quota import check_publish_quota, find_free_day  # noqa: E402


def test_blocks_at_daily_cap(monkeypatch):
    cfg = {"agent6": {"per_network_daily_limit": {"max_posts_per_day": 4, "carousel_max": 1, "video_max": 3}}}
    monkeypatch.setattr(
        "daily_quota.count_today_posts",
        lambda _n, _c, _off=0: {"total": 4, "carousel": 1, "video": 3},
    )
    reason = check_publish_quota("instagram", cfg, upload_video=False)
    assert reason and "4/4" in reason


def test_allows_under_cap(monkeypatch):
    cfg = {"agent6": {"per_network_daily_limit": {"max_posts_per_day": 4}}}
    monkeypatch.setattr(
        "daily_quota.count_today_posts",
        lambda _n, _c, _off=0: {"total": 2, "carousel": 1, "video": 1},
    )
    assert check_publish_quota("tiktok", cfg, upload_video=True) is None


def test_find_free_day_shifts_to_tomorrow(monkeypatch):
    """Сегодня квота занята (4/4) → слот на завтра (offset=1), не skip."""
    cfg = {"agent6": {"per_network_daily_limit": {"max_posts_per_day": 4}}}
    per_day = {0: 4, 1: 0}
    monkeypatch.setattr(
        "daily_quota.count_today_posts",
        lambda _n, _c, off=0: {"total": per_day.get(off, 0), "carousel": 0, "video": 0},
    )
    offset, today_reason = find_free_day("instagram", cfg, upload_video=False)
    assert offset == 1
    assert today_reason and "4/4" in today_reason


def test_find_free_day_today_open(monkeypatch):
    cfg = {"agent6": {"per_network_daily_limit": {"max_posts_per_day": 4}}}
    monkeypatch.setattr(
        "daily_quota.count_today_posts",
        lambda _n, _c, off=0: {"total": 1, "carousel": 0, "video": 1},
    )
    offset, today_reason = find_free_day("tiktok", cfg, upload_video=True)
    assert offset == 0
    assert today_reason is None


def test_find_free_day_no_slot(monkeypatch):
    """Все дни в пределах lookahead заняты → (None, причина)."""
    cfg = {"agent6": {"per_network_daily_limit": {"max_posts_per_day": 1}, "quota_lookahead_days": 2}}
    monkeypatch.setattr(
        "daily_quota.count_today_posts",
        lambda _n, _c, off=0: {"total": 1, "carousel": 1, "video": 0},
    )
    offset, today_reason = find_free_day("instagram", cfg, upload_video=False)
    assert offset is None
    assert today_reason
