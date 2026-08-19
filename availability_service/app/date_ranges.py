"""Build contiguous date ranges from sorted dates."""
from __future__ import annotations

from datetime import date, timedelta
from typing import TypedDict


class DateRangeDict(TypedDict):
    from_: str  # will serialize as "from" in output
    to: str


def build_date_ranges(dates: list[date]) -> list[dict[str, str]]:
    """Merge consecutive dates into ranges. Single day → from == to."""
    if not dates:
        return []
    sorted_dates = sorted(set(dates))
    ranges: list[dict[str, str]] = []
    start = sorted_dates[0]
    prev = start
    for current in sorted_dates[1:]:
        if current == prev + timedelta(days=1):
            prev = current
            continue
        ranges.append({"from": start.isoformat(), "to": prev.isoformat()})
        start = current
        prev = current
    ranges.append({"from": start.isoformat(), "to": prev.isoformat()})
    return ranges
