"""Calendar snapshot coverage, staleness, refresh helpers."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

from availability import fetch_calendar_days
from monthly_pricing import month_bounds
from pricing_config import calendar_snapshot_ttl_seconds
from pricing_state import deserialize_calendar, serialize_calendar


def month_has_calendar_data(
    availability: dict[date, bool], year: int, month: int
) -> bool:
    """True if at least one day of the month exists in the snapshot."""
    first, last = month_bounds(year, month)
    day = first
    while day <= last:
        if day in availability:
            return True
        day += timedelta(days=1)
    return False


def calendar_snapshot_age_seconds(obj: dict[str, Any]) -> float | None:
    saved_at = obj.get("calendar_saved_at")
    if saved_at is None:
        return None
    try:
        return max(0.0, time.time() - float(saved_at))
    except (TypeError, ValueError):
        return None


def calendar_snapshot_is_stale(obj: dict[str, Any]) -> bool:
    ttl = calendar_snapshot_ttl_seconds()
    if ttl <= 0:
        return False
    age = calendar_snapshot_age_seconds(obj)
    if age is None:
        return True
    return age > ttl


def needs_calendar_refresh(
    obj: dict[str, Any],
    year: int,
    month: int,
) -> tuple[bool, str]:
    """(should_refresh, reason). reason: month_missing | stale | empty."""
    availability = deserialize_calendar(obj.get("calendar"))
    if not availability:
        return True, "empty"
    if not month_has_calendar_data(availability, year, month):
        return True, "month_missing"
    if calendar_snapshot_is_stale(obj):
        return True, "stale"
    return False, ""


def refresh_calendar_snapshot(listing_url: str) -> dict[date, bool]:
    return fetch_calendar_days(listing_url)


def apply_calendar_to_object(obj: dict[str, Any], availability: dict[date, bool]) -> dict[str, Any]:
    obj["calendar"] = serialize_calendar(availability)
    obj["calendar_saved_at"] = time.time()
    return obj
