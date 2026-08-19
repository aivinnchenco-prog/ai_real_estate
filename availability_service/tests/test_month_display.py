"""Tests for month display formatting and blocked-range labels."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from availability_service.app.date_ranges import build_date_ranges
from availability_service.app.month_display import (
    MonthDisplayConsistencyError,
    MonthDisplayStatus,
    blocked_days_in_month,
    build_month_display_rows,
    evaluate_month_display_status,
    format_blocked_ranges_for_month,
    format_notion_month_cell,
    format_price_thb,
)
from availability_service.app.models import MonthWindowItem
from availability_service.app.status_mapper import month_bounds


def _fill_month(year: int, month: int, available: bool) -> dict[date, bool]:
    first, last = month_bounds(year, month)
    out: dict[date, bool] = {}
    day = first
    while day <= last:
        out[day] = available
        day += timedelta(days=1)
    return out


def _blocked_days(year: int, month: int, day_nums: list[int]) -> list[date]:
    return [date(year, month, d) for d in day_nums]


def test_format_blocked_ranges_sep_example():
    days = _blocked_days(2026, 9, [9, 10, 11, 12, 21, 22, 23, 24])
    assert format_blocked_ranges_for_month(days) == "09–12, 21–24"


def test_format_blocked_ranges_single_day():
    days = _blocked_days(2026, 9, [17])
    assert format_blocked_ranges_for_month(days) == "17"


def test_format_blocked_ranges_mixed():
    days = _blocked_days(2026, 9, [3, 4, 5, 11, 19, 20, 21, 22])
    assert format_blocked_ranges_for_month(days) == "03–05, 11, 19–22"


def test_build_date_ranges_canonical():
    days = _blocked_days(2026, 9, [9, 10, 11, 12, 21, 22, 23, 24])
    ranges = build_date_ranges(days)
    assert ranges == [
        {"from": "2026-09-09", "to": "2026-09-12"},
        {"from": "2026-09-21", "to": "2026-09-24"},
    ]


def test_partial_with_price_and_ranges():
    cell = format_notion_month_cell(
        MonthDisplayStatus.PARTIAL,
        102000,
        blocked_ranges_display="09–12, 21–24",
    )
    assert cell == "Частично · 102 000 ฿ 🔴 09–12, 21–24"


def test_partial_without_price_and_ranges():
    cell = format_notion_month_cell(
        MonthDisplayStatus.PARTIAL,
        None,
        blocked_ranges_display="09–12, 21–24",
    )
    assert cell == "Частично 🔴 09–12, 21–24"


def test_fully_available_with_price():
    assert format_notion_month_cell(MonthDisplayStatus.FULLY_AVAILABLE, 102000) == "102 000 ฿"


def test_fully_available_no_price():
    assert format_notion_month_cell(MonthDisplayStatus.FULLY_AVAILABLE, None) == "Доступен"


def test_unavailable():
    assert format_notion_month_cell(MonthDisplayStatus.UNAVAILABLE, None) == "Недоступен 🔴 весь месяц"


def test_unknown():
    assert format_notion_month_cell(MonthDisplayStatus.UNKNOWN, None) == "Неизвестно"


def test_partial_fallback_format_only():
    assert format_notion_month_cell(MonthDisplayStatus.PARTIAL, 102000) == "Частично · 102 000 ฿"
    assert format_notion_month_cell(MonthDisplayStatus.PARTIAL, None) == "Частично"


def test_partial_without_blocked_dates_fallback_with_warning():
    from unittest.mock import patch

    window = [MonthWindowItem(2026, 9, "2026-09", "Sep 26")]
    cal = _fill_month(2026, 9, True)
    with patch(
        "availability_service.app.month_display.evaluate_month_display_status",
        return_value=MonthDisplayStatus.PARTIAL,
    ):
        result = build_month_display_rows(window, cal, {})
    assert result.rows[0][2] == "Частично"
    assert len(result.warnings) == 1


def test_fully_available_with_blocked_day_raises():
    cal = _fill_month(2026, 9, True)
    first, _ = month_bounds(2026, 9)
    cal[first] = False
    # All days except one blocked - that's PARTIAL not FULLY_AVAILABLE
    # For FULLY_AVAILABLE + blocked: all days marked available in coverage but one blocked in dict
    cal = _fill_month(2026, 9, True)
    cal[date(2026, 9, 15)] = False
    # evaluate would be PARTIAL. Need FULLY_AVAILABLE status with blocked - call format directly
    with pytest.raises(MonthDisplayConsistencyError):
        format_notion_month_cell(
            MonthDisplayStatus.FULLY_AVAILABLE,
            102000,
            blocked_ranges_display="15",
        )


def test_fully_available_blocked_in_build_raises():
    from unittest.mock import patch

    window = [MonthWindowItem(2026, 12, "2026-12", "Dec 26")]
    cal = _fill_month(2026, 12, True)
    cal[date(2026, 12, 10)] = False
    with patch(
        "availability_service.app.month_display.evaluate_month_display_status",
        return_value=MonthDisplayStatus.FULLY_AVAILABLE,
    ):
        with pytest.raises(MonthDisplayConsistencyError):
            build_month_display_rows(window, cal, {})


def test_evaluate_full_month_available():
    cal = _fill_month(2026, 9, True)
    assert evaluate_month_display_status(cal, 2026, 9) == MonthDisplayStatus.FULLY_AVAILABLE


def test_evaluate_partial():
    cal = _fill_month(2026, 9, True)
    first, _ = month_bounds(2026, 9)
    cal[first] = False
    assert evaluate_month_display_status(cal, 2026, 9) == MonthDisplayStatus.PARTIAL


def test_missing_aug_2027_calendar_tail_is_unknown():
    """Sep 2026–Jul 2027 daily rows (334) leave Aug 2027 without coverage → UNKNOWN."""
    cal: dict[date, bool] = {}
    start = date(2026, 9, 1)
    end = date(2027, 7, 31)
    day = start
    while day <= end:
        cal[day] = True
        day += timedelta(days=1)
    assert len(cal) == 334
    assert evaluate_month_display_status(cal, 2027, 8) == MonthDisplayStatus.UNKNOWN
    assert evaluate_month_display_status(cal, 2027, 7) == MonthDisplayStatus.FULLY_AVAILABLE


def test_blocked_days_in_month():
    cal = _fill_month(2026, 9, True)
    cal[date(2026, 9, 9)] = False
    cal[date(2026, 9, 10)] = False
    blocked = blocked_days_in_month(cal, 2026, 9)
    assert blocked == [date(2026, 9, 9), date(2026, 9, 10)]


def test_format_price_thb():
    assert format_price_thb(145000) == "145 000 ฿"
