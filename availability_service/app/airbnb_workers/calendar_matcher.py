"""Detect Airbnb calendar API responses (URL + JSON shape)."""

from __future__ import annotations

from typing import Any

_CALENDAR_URL_MARKERS = (
    "pdpavailabilitycalendar",
    "stayspdpavailabilitycalendar",
    "pdp_availability_calendar",
    "availabilitycalendar",
)


def is_calendar_api_url(url: str) -> bool:
    u = (url or "").lower()
    if "graphql" in u or "api/v" in u or "/stays/" in u:
        return any(marker in u for marker in _CALENDAR_URL_MARKERS)
    return any(marker in u for marker in _CALENDAR_URL_MARKERS)


def is_calendar_api_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    merlin = data.get("merlin")
    if isinstance(merlin, dict) and "pdpAvailabilityCalendar" in merlin:
        cal = merlin["pdpAvailabilityCalendar"]
        if isinstance(cal, dict) and isinstance(cal.get("calendarMonths"), list):
            return True
    # Alternate top-level shapes seen in GraphQL batches
    for key in ("pdpAvailabilityCalendar", "staysPdpAvailabilityCalendar"):
        node = data.get(key)
        if isinstance(node, dict) and isinstance(node.get("calendarMonths"), list):
            return True
    return False


def summarize_network_name(url: str, method: str = "GET") -> str:
    u = (url or "").lower()
    if "pdpavailabilitycalendar" in u:
        return "PdpAvailabilityCalendar"
    if "stayspdpavailabilitycalendar" in u:
        return "StaysPdpAvailabilityCalendar"
    if "graphql" in u:
        return f"GraphQL:{method.upper()}"
    if "availability" in u:
        return "availability:*"
    if "/rooms/" in u:
        return "rooms:*"
    return method.upper()
