"""Map Airbnb calendar + pricing entries to MonthAvailability.

Calendar is the primary source for availability status.
Price fields are attached separately and never override calendar status.

NOTE: Month-level status here is MONTH DISPLAY STATUS (summary for UI / Notion
month columns). It is NOT a guarantee for any client stay period inside the month.
Exact search uses CalendarDay + stay_matcher.check_stay_availability().
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from enum import Enum

from .models import AvailabilityStatus, CalendarDay, MonthAvailability, MonthWindowItem, calendar_day
from .models import DailyAvailabilityStatus, SourceKind


class AirbnbMonthPolicy(str, Enum):
    """Explicit month-fit policy (configurable, tested independently)."""

    FULL_MONTH_REQUIRED = "FULL_MONTH_REQUIRED"
    CONTIGUOUS_STAY_REQUIRED = "CONTIGUOUS_STAY_REQUIRED"


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def calendar_to_dict(days: list[CalendarDay]) -> dict[date, bool]:
    return {day.date: day.available for day in days}


def calendar_status_dict(days: list[CalendarDay]) -> dict[date, DailyAvailabilityStatus]:
    return {day.date: day.status for day in days}


def normalize_calendar(raw: dict[date, bool] | list[CalendarDay]) -> list[CalendarDay]:
    if isinstance(raw, list):
        return list(raw)
    return [
        calendar_day(
            day,
            available=available,
            source=SourceKind.AIRBNB,
            source_state="airbnb_pdp",
        )
        for day, available in sorted(raw.items())
    ]


def month_calendar_coverage(
    calendar: dict[date, bool],
    year: int,
    month: int,
) -> tuple[int, int, int]:
    """Return (days_with_data, available_days, total_days_in_month)."""
    first, last = month_bounds(year, month)
    total = (last - first).days + 1
    seen = 0
    available = 0
    day = first
    while day <= last:
        if day in calendar:
            seen += 1
            if calendar[day]:
                available += 1
        day += timedelta(days=1)
    return seen, available, total


def longest_available_segment(
    calendar: dict[date, bool],
    year: int,
    month: int,
) -> tuple[date, date] | None:
    first, last = month_bounds(year, month)
    best: tuple[date, date] | None = None
    seg_start: date | None = None
    day = first
    while day <= last:
        if calendar.get(day):
            if seg_start is None:
                seg_start = day
            if best is None or (day - seg_start) > (best[1] - best[0]):
                best = (seg_start, day)
        else:
            seg_start = None
        day += timedelta(days=1)
    return best


def segment_days(segment: tuple[date, date]) -> int:
    return (segment[1] - segment[0]).days + 1


def month_fully_available(calendar: dict[date, bool], year: int, month: int) -> bool | None:
    """True = entire month available, False = some blocked day, None = incomplete data."""
    first, last = month_bounds(year, month)
    seen = False
    day = first
    while day <= last:
        if day in calendar:
            seen = True
            if not calendar[day]:
                return False
        else:
            return None if not seen else False
        day += timedelta(days=1)
    return True if seen else None


def evaluate_month_calendar_status(
    calendar: dict[date, bool],
    year: int,
    month: int,
    policy: AirbnbMonthPolicy,
    *,
    min_contiguous_days: int = 5,
) -> AvailabilityStatus:
    if not calendar:
        return AvailabilityStatus.UNKNOWN

    seen, available_days, total_days = month_calendar_coverage(calendar, year, month)
    if seen == 0:
        return AvailabilityStatus.UNKNOWN

    if policy == AirbnbMonthPolicy.FULL_MONTH_REQUIRED:
        fully = month_fully_available(calendar, year, month)
        if fully is True:
            return AvailabilityStatus.AVAILABLE
        if fully is False:
            return AvailabilityStatus.UNAVAILABLE
        return AvailabilityStatus.UNKNOWN

    if policy == AirbnbMonthPolicy.CONTIGUOUS_STAY_REQUIRED:
        if seen < total_days:
            return AvailabilityStatus.UNKNOWN
        segment = longest_available_segment(calendar, year, month)
        if segment is None:
            return AvailabilityStatus.UNAVAILABLE
        length = segment_days(segment)
        if length >= min_contiguous_days:
            return AvailabilityStatus.AVAILABLE
        return AvailabilityStatus.UNAVAILABLE

    raise ValueError(f"Unsupported policy: {policy!r}")


def merge_calendar_and_price(
    year: int,
    month: int,
    calendar_status: AvailabilityStatus,
    price_entry: dict | None,
    *,
    currency: str = "THB",
) -> MonthAvailability:
    entry = price_entry or {}
    price = entry.get("price")
    price_f = float(price) if price is not None else None
    return MonthAvailability(
        year=year,
        month=month,
        status=calendar_status,
        price=price_f,
        currency=currency,
        pricing_status=str(entry.get("status") or "") or None,
        period_used=str(entry.get("period_used") or "") or None,
        based_on_days=int(entry["based_on_days"]) if entry.get("based_on_days") is not None else None,
        note=str(entry.get("note") or "") or None,
    )


def build_month_availabilities(
    window: list[MonthWindowItem],
    calendar: dict[date, bool] | list[CalendarDay],
    price_entries: dict[str, dict],
    policy: AirbnbMonthPolicy,
    *,
    min_contiguous_days: int = 5,
    currency: str = "THB",
) -> list[MonthAvailability]:
    cal_dict = calendar if isinstance(calendar, dict) else calendar_to_dict(calendar)
    results: list[MonthAvailability] = []
    for item in window:
        cal_status = evaluate_month_calendar_status(
            cal_dict,
            item.year,
            item.month,
            policy,
            min_contiguous_days=min_contiguous_days,
        )
        entry = price_entries.get(item.key) or {}
        results.append(
            merge_calendar_and_price(
                item.year,
                item.month,
                cal_status,
                entry,
                currency=currency,
            )
        )
    return results
