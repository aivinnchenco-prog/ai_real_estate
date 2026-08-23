#!/usr/bin/env python3
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fb_daily_report import (
    day_bounds_utc,
    filter_posts,
    format_report,
    group_label,
    is_bangkok_18,
    report_day,
    summarize_groups,
    summarize_marketplace,
)

UTC = timezone.utc
BANGKOK = ZoneInfo("Asia/Bangkok")


def test_report_day_before_posting_window_uses_yesterday():
    now = datetime(2026, 8, 23, 8, 0, tzinfo=BANGKOK)
    assert report_day(now) == date(2026, 8, 22)


def test_report_day_during_posting_uses_today():
    now = datetime(2026, 8, 22, 16, 0, tzinfo=BANGKOK)
    assert report_day(now) == date(2026, 8, 22)


def test_day_bounds_cover_bangkok_calendar_day():
    start, end = day_bounds_utc(date(2026, 8, 21))
    assert start.astimezone(BANGKOK).hour == 0
    assert (end - start).total_seconds() == 24 * 3600


def test_filter_and_summarize_groups():
    posts = [
        {
            "ts": "2026-08-21T02:33:06+00:00",
            "group": "https://www.facebook.com/groups/PhuketNotizie/",
            "object_id": "A_20260820_001",
            "post_url": "https://www.facebook.com/groups/PhuketNotizie/posts/1/",
        },
        {
            "ts": "2026-08-21T03:04:43+00:00",
            "group": "https://www.facebook.com/groups/pn.thailand.hm/",
            "object_id": "A_20260820_001",
            "post_url": "",
        },
        {
            "ts": "2026-08-20T12:12:08+00:00",
            "group": "https://www.facebook.com/groups/rentinphuket/",
            "object_id": "A_20260817_001",
            "post_url": "",
        },
    ]
    start, end = day_bounds_utc(date(2026, 8, 21))
    day_posts = filter_posts(posts, start, end)
    summary = summarize_groups(day_posts)
    assert summary["posts"] == 2
    assert summary["unique_groups"] == 2
    assert summary["with_url"] == 1
    assert summary["objects"][0]["object_id"] == "A_20260820_001"
    assert summary["objects"][0]["posts"] == 2


def test_marketplace_summary_and_format():
    posts = [
        {
            "ts": "2026-08-22T07:32:11+00:00",
            "group": "marketplace",
            "object_id": "A_20260820_002",
            "post_url": "",
        }
    ]
    start, end = day_bounds_utc(date(2026, 8, 22))
    summary = summarize_marketplace(filter_posts(posts, start, end))
    text = format_report(
        date(2026, 8, 22),
        summarize_groups([]),
        summary,
        queue_groups=[],
        queue_marketplace=["A_20260820_001"],
    )
    assert "Marketplace: 1 объявление" in text
    assert "A_20260820_002" in text
    assert "Очередь групп: пусто" in text
    assert "A_20260820_001" in text


def test_group_label_uses_slug():
    assert group_label("https://www.facebook.com/groups/rentinphuket/") == "rentinphuket"


def test_bangkok_18_gate():
    assert is_bangkok_18(datetime(2026, 8, 22, 18, 5, tzinfo=BANGKOK)) is True
    assert is_bangkok_18(datetime(2026, 8, 22, 17, 59, tzinfo=BANGKOK)) is False
