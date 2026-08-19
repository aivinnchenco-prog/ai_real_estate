"""Exact stay availability: check-in date + stay period in calendar months.

Daily Availability DB (CalendarDay / availability_calendar_days) is the source of truth
for exact dates. Month display status does not affect this matcher.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from .date_ranges import build_date_ranges
from .models import (
    AvailabilityFreshness,
    AvailabilityStatus,
    CalendarDay,
    DailyAvailabilityStatus,
    StayAvailabilityResult,
    calendar_day,
)


def add_calendar_months(check_in: date, stay_months: int) -> date:
    """Checkout date: same day-of-month after N calendar months (clamped to month-end)."""
    if stay_months < 1:
        raise ValueError("stay_months must be >= 1")
    month_index = check_in.month - 1 + stay_months
    year = check_in.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(check_in.day, last_day)
    return date(year, month, day)


def iter_stay_days(check_in: date, check_out: date):
    """Nights/days of stay: check_in inclusive, check_out exclusive."""
    day = check_in
    while day < check_out:
        yield day
        day += timedelta(days=1)


def _calendar_lookup(
    calendar_days: list[CalendarDay] | dict[date, bool] | dict[date, DailyAvailabilityStatus],
) -> dict[date, DailyAvailabilityStatus | None]:
    if isinstance(calendar_days, dict):
        out: dict[date, DailyAvailabilityStatus | None] = {}
        for day, value in calendar_days.items():
            if isinstance(value, DailyAvailabilityStatus):
                out[day] = value
            elif isinstance(value, bool):
                out[day] = (
                    DailyAvailabilityStatus.AVAILABLE
                    if value
                    else DailyAvailabilityStatus.BLOCKED
                )
            else:
                out[day] = DailyAvailabilityStatus.UNKNOWN
        return out
    return {item.date: item.status for item in calendar_days}


def check_stay_availability(
    object_id: str,
    check_in: date,
    stay_months: int,
    calendar_days: list[CalendarDay] | dict[date, bool] | dict[date, DailyAvailabilityStatus],
    *,
    last_checked_at=None,
    freshness: AvailabilityFreshness = AvailabilityFreshness.MISSING,
) -> StayAvailabilityResult:
    """Match exact stay against stored daily calendar (no price influence)."""
    check_out = add_calendar_months(check_in, stay_months)
    cal = _calendar_lookup(calendar_days)
    stay_days = list(iter_stay_days(check_in, check_out))

    available_dates: list[date] = []
    blocked_dates: list[date] = []
    unknown_dates: list[date] = []

    for day in stay_days:
        status = cal.get(day)
        if status is None:
            unknown_dates.append(day)
        elif status == DailyAvailabilityStatus.BLOCKED:
            blocked_dates.append(day)
        elif status == DailyAvailabilityStatus.AVAILABLE:
            available_dates.append(day)
        else:
            unknown_dates.append(day)

    if blocked_dates:
        stay_status = AvailabilityStatus.UNAVAILABLE
    elif unknown_dates:
        stay_status = AvailabilityStatus.UNKNOWN
    else:
        stay_status = AvailabilityStatus.AVAILABLE

    return StayAvailabilityResult(
        object_id=object_id,
        status=stay_status,
        check_in=check_in,
        check_out=check_out,
        stay_months=stay_months,
        checked_days_count=len(stay_days),
        available_dates=available_dates,
        blocked_dates=blocked_dates,
        unknown_dates=unknown_dates,
        available_ranges=build_date_ranges(available_dates),
        blocked_ranges=build_date_ranges(blocked_dates),
        unknown_ranges=build_date_ranges(unknown_dates),
        last_checked_at=last_checked_at,
        freshness=freshness,
        unavailable_dates=blocked_dates,
        missing_dates=unknown_dates,
        checked_days=stay_days,
    )
