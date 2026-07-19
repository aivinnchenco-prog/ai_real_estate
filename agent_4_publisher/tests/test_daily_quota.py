#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from daily_quota import check_publish_quota  # noqa: E402


def test_blocks_at_daily_cap(monkeypatch):
    cfg = {"agent6": {"per_network_daily_limit": {"max_posts_per_day": 4, "carousel_max": 1, "video_max": 3}}}
    monkeypatch.setattr(
        "daily_quota.count_today_posts",
        lambda _n, _c: {"total": 4, "carousel": 1, "video": 3},
    )
    reason = check_publish_quota("instagram", cfg, upload_video=False)
    assert reason and "4/4" in reason


def test_allows_under_cap(monkeypatch):
    cfg = {"agent6": {"per_network_daily_limit": {"max_posts_per_day": 4}}}
    monkeypatch.setattr(
        "daily_quota.count_today_posts",
        lambda _n, _c: {"total": 2, "carousel": 1, "video": 1},
    )
    assert check_publish_quota("tiktok", cfg, upload_video=True) is None
