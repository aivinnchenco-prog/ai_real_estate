"""Format calendar and blocked-range summaries for probe reports."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta

from .models import MONTH_ABBR
from .status_mapper import month_bounds


def calendar_stats(cal: dict[date, bool]) -> dict:
    if not cal:
        return {
            "calendar_days_received": 0,
            "first_calendar_date": None,
            "last_calendar_date": None,
            "available_days_count": 0,
            "blocked_days_count": 0,
        }
    dates = sorted(cal)
    return {
        "calendar_days_received": len(cal),
        "first_calendar_date": dates[0],
        "last_calendar_date": dates[-1],
        "available_days_count": sum(1 for v in cal.values() if v),
        "blocked_days_count": sum(1 for v in cal.values() if not v),
    }


def format_daily_calendar(cal: dict[date, bool]) -> str:
    if not cal:
        return "(no calendar data)\n"
    by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
    for d in sorted(cal):
        by_month[(d.year, d.month)].append(d)
    lines: list[str] = []
    for year, month in sorted(by_month):
        label = f"{MONTH_ABBR[month - 1].upper()} {year}"
        lines.append(label)
        for d in by_month[(year, month)]:
            state = "AVAILABLE" if cal[d] else "BLOCKED"
            lines.append(f"{d.day:02d} {state}")
        lines.append("")
    return "\n".join(lines)


def _ranges_for_month(
    cal: dict[date, bool],
    year: int,
    month: int,
) -> list[tuple[str, date | None, date | None]]:
    """Return list of (label, start, end) for blocked ranges or special labels."""
    first, last = month_bounds(year, month)
    total_days = (last - first).days + 1
    seen = 0
    available_count = 0
    blocked_count = 0
    day = first
    while day <= last:
        if day in cal:
            seen += 1
            if cal[day]:
                available_count += 1
            else:
                blocked_count += 1
        day += timedelta(days=1)

    if seen == 0:
        return [("INCOMPLETE / UNKNOWN", None, None)]
    if seen < total_days:
        if blocked_count == 0 and available_count == seen:
            return [("PARTIAL DATA (missing days)", None, None)]
        if blocked_count > 0:
            ranges = _blocked_ranges_in_month(cal, year, month)
            ranges.insert(0, ("INCOMPLETE / UNKNOWN (missing days)", None, None))
            return ranges
        return [("INCOMPLETE / UNKNOWN", None, None)]
    if blocked_count == 0:
        return [("FULLY AVAILABLE", None, None)]
    if available_count == 0:
        return [("FULLY BLOCKED", None, None)]
    return _blocked_ranges_in_month(cal, year, month)


def _blocked_ranges_in_month(cal: dict[date, bool], year: int, month: int) -> list[tuple[str, date, date]]:
    first, last = month_bounds(year, month)
    ranges: list[tuple[str, date, date]] = []
    start: date | None = None
    day = first
    while day <= last:
        blocked = day in cal and not cal[day]
        if blocked:
            if start is None:
                start = day
        else:
            if start is not None:
                ranges.append(("BLOCKED", start, day - timedelta(days=1)))
                start = None
        day += timedelta(days=1)
    if start is not None:
        ranges.append(("BLOCKED", start, last))
    return ranges


def format_blocked_ranges_summary(
    cal: dict[date, bool],
    window_months: list[tuple[int, int]],
) -> str:
    lines: list[str] = []
    for year, month in window_months:
        label = f"{MONTH_ABBR[month - 1]} {year}:"
        entries = _ranges_for_month(cal, year, month)
        parts: list[str] = []
        for kind, start, end in entries:
            if start is None:
                parts.append(kind)
            else:
                if start == end:
                    parts.append(f"{start.day:02d} BLOCKED")
                else:
                    parts.append(f"{start.day:02d}–{end.day:02d} BLOCKED")
        lines.append(f"{label}")
        for part in parts:
            lines.append(f"  {part}")
        lines.append("")
    return "\n".join(lines)
