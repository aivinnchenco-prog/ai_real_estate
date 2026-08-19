"""Website month-period filter (V1 business rule)."""
from __future__ import annotations

from .month_display import MonthDisplayStatus


def normalize_month_display_status(value: MonthDisplayStatus | str) -> MonthDisplayStatus:
    if isinstance(value, MonthDisplayStatus):
        return value
    return MonthDisplayStatus(value)


def matches_month_period(display_statuses: list[MonthDisplayStatus | str]) -> bool:
    """MATCH only when every month in the requested period is FULLY_AVAILABLE.

    PARTIAL / UNAVAILABLE / UNKNOWN in any month → NO MATCH.
    Empty period → NO MATCH.
    """
    if not display_statuses:
        return False
    for status in display_statuses:
        if normalize_month_display_status(status) != MonthDisplayStatus.FULLY_AVAILABLE:
            return False
    return True
