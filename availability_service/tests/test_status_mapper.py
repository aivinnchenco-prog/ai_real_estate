"""Fixture tests for Airbnb month status mapping (calendar + price, no live network)."""
from __future__ import annotations

from datetime import date, timedelta

from availability_service.app.models import AvailabilityStatus, build_month_window
from availability_service.app.status_mapper import (
    AirbnbMonthPolicy,
    build_month_availabilities,
    evaluate_month_calendar_status,
    merge_calendar_and_price,
    month_bounds,
)


def _fill_month(year: int, month: int, available: bool) -> dict[date, bool]:
    first, last = month_bounds(year, month)
    out: dict[date, bool] = {}
    day = first
    while day <= last:
        out[day] = available
        day += timedelta(days=1)
    return out


def _partial_month(year: int, month: int, start_day: int, end_day: int) -> dict[date, bool]:
    first, _ = month_bounds(year, month)
    out: dict[date, bool] = {}
    for offset in range(start_day - 1, end_day):
        out[first + timedelta(days=offset)] = True
    return out


def test_a_full_month_available():
    cal = _fill_month(2026, 9, True)
    status = evaluate_month_calendar_status(
        cal, 2026, 9, AirbnbMonthPolicy.FULL_MONTH_REQUIRED
    )
    assert status == AvailabilityStatus.AVAILABLE


def test_b_full_month_unavailable():
    cal = _fill_month(2026, 9, False)
    status = evaluate_month_calendar_status(
        cal, 2026, 9, AirbnbMonthPolicy.FULL_MONTH_REQUIRED
    )
    assert status == AvailabilityStatus.UNAVAILABLE


def test_c_partial_month_contiguous_policy():
    cal = _fill_month(2026, 9, False)
    first, _ = month_bounds(2026, 9)
    for i in range(10):
        cal[first + timedelta(days=i)] = True
    status = evaluate_month_calendar_status(
        cal,
        2026,
        9,
        AirbnbMonthPolicy.CONTIGUOUS_STAY_REQUIRED,
        min_contiguous_days=5,
    )
    assert status == AvailabilityStatus.AVAILABLE


def test_c_partial_month_full_month_policy_unavailable():
    cal = _fill_month(2026, 9, False)
    first, _ = month_bounds(2026, 9)
    for i in range(10):
        cal[first + timedelta(days=i)] = True
    status = evaluate_month_calendar_status(
        cal, 2026, 9, AirbnbMonthPolicy.FULL_MONTH_REQUIRED
    )
    assert status == AvailabilityStatus.UNAVAILABLE


def test_d_calendar_missing_unknown():
    status = evaluate_month_calendar_status(
        {}, 2026, 9, AirbnbMonthPolicy.FULL_MONTH_REQUIRED
    )
    assert status == AvailabilityStatus.UNKNOWN


def test_e_incomplete_calendar_unknown():
    cal = _partial_month(2026, 9, 1, 5)
    status = evaluate_month_calendar_status(
        cal,
        2026,
        9,
        AirbnbMonthPolicy.CONTIGUOUS_STAY_REQUIRED,
        min_contiguous_days=5,
    )
    assert status == AvailabilityStatus.UNKNOWN


def test_f_price_present_calendar_unavailable():
    cal = _fill_month(2026, 9, False)
    entry = {"price": 50000, "status": "monthly", "period_used": "2026-09-01/2026-09-30"}
    cal_status = evaluate_month_calendar_status(
        cal, 2026, 9, AirbnbMonthPolicy.FULL_MONTH_REQUIRED
    )
    merged = merge_calendar_and_price(2026, 9, cal_status, entry)
    assert merged.status == AvailabilityStatus.UNAVAILABLE
    assert merged.price == 50000.0
    assert merged.pricing_status == "monthly"


def test_g_no_price_calendar_available():
    cal = _fill_month(2026, 9, True)
    cal_status = evaluate_month_calendar_status(
        cal, 2026, 9, AirbnbMonthPolicy.FULL_MONTH_REQUIRED
    )
    merged = merge_calendar_and_price(2026, 9, cal_status, None)
    assert merged.status == AvailabilityStatus.AVAILABLE
    assert merged.price is None


def test_h_pricing_status_monthly():
    merged = merge_calendar_and_price(
        2026,
        9,
        AvailabilityStatus.AVAILABLE,
        {"price": 40000, "status": "monthly", "period_used": "2026-09-01/2026-09-30"},
    )
    assert merged.pricing_status == "monthly"
    assert merged.period_used == "2026-09-01/2026-09-30"


def test_i_pricing_status_prorated():
    merged = merge_calendar_and_price(
        2026,
        9,
        AvailabilityStatus.AVAILABLE,
        {
            "price": 42000,
            "status": "prorated",
            "based_on_days": 14,
            "period_used": "2026-09-10/2026-09-23",
        },
    )
    assert merged.pricing_status == "prorated"
    assert merged.based_on_days == 14


def test_j_pricing_status_insufficient_data():
    merged = merge_calendar_and_price(
        2026,
        9,
        AvailabilityStatus.UNAVAILABLE,
        {"price": None, "status": "insufficient_data", "available_days": 2},
    )
    assert merged.pricing_status == "insufficient_data"
    assert merged.price is None
    assert merged.status == AvailabilityStatus.UNAVAILABLE


def test_k_rolling_window_twelve_months():
    start = date(2026, 9, 1)
    window = build_month_window(start, months=12)
    cal: dict[date, bool] = {}
    prices: dict[str, dict] = {}
    for item in window:
        cal.update(_fill_month(item.year, item.month, True))
        prices[item.key] = {
            "price": 50000,
            "status": "monthly",
            "period_used": f"{item.key}-01/{item.key}-28",
        }
    rows = build_month_availabilities(
        window,
        cal,
        prices,
        AirbnbMonthPolicy.FULL_MONTH_REQUIRED,
    )
    assert len(rows) == 12
    assert rows[0].year == 2026 and rows[0].month == 9
    assert rows[-1].year == 2027 and rows[-1].month == 8
    assert all(r.status == AvailabilityStatus.AVAILABLE for r in rows)
    assert all(r.price == 50000.0 for r in rows)
