"""Compatibility shim. Canonical import: ``agent8.envoy.calendar_check``."""
import requests  # noqa: F401 — tests monkeypatch agent8.calendar_check.requests

from agent8.envoy.calendar_check import (
    _blocked_days_from_sheet,
    _parse_ics_dates,
    check_calendar_dates,
    check_gsheet_dates,
    check_ical_dates,
    format_busy_ranges,
    notion_update_from_precheck,
)

__all__ = [
    "_blocked_days_from_sheet",
    "_parse_ics_dates",
    "check_calendar_dates",
    "check_gsheet_dates",
    "check_ical_dates",
    "format_busy_ranges",
    "notion_update_from_precheck",
    "requests",
]
