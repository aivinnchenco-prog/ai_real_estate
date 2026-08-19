"""Month display status and Notion cell formatting (calendar-driven, not stay policy)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

from .date_ranges import build_date_ranges
from .models import CalendarDay, MonthAvailability, MonthWindowItem
from .status_mapper import month_bounds, month_calendar_coverage, normalize_calendar

logger = logging.getLogger(__name__)


class MonthDisplayStatus(str, Enum):
    FULLY_AVAILABLE = "FULLY_AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class MonthDisplayConsistencyError(RuntimeError):
    """Calendar status disagrees with blocked-day facts (e.g. FULLY_AVAILABLE + BLOCKED day)."""


@dataclass
class MonthDisplayBuildResult:
    rows: list[tuple[MonthWindowItem, MonthDisplayStatus, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked_ranges_by_key: dict[str, str] = field(default_factory=dict)


def evaluate_month_display_status(
    calendar: dict[date, bool],
    year: int,
    month: int,
) -> MonthDisplayStatus:
    """Derive human month summary from daily calendar (FULL_MONTH coverage, not contiguous stay)."""
    if not calendar:
        return MonthDisplayStatus.UNKNOWN

    seen, available_days, total_days = month_calendar_coverage(calendar, year, month)
    if seen == 0:
        return MonthDisplayStatus.UNKNOWN
    if seen < total_days:
        return MonthDisplayStatus.UNKNOWN
    if available_days == total_days:
        return MonthDisplayStatus.FULLY_AVAILABLE
    if available_days == 0:
        return MonthDisplayStatus.UNAVAILABLE
    return MonthDisplayStatus.PARTIAL


def blocked_days_in_month(
    calendar: dict[date, bool],
    year: int,
    month: int,
) -> list[date]:
    """BLOCKED days present in calendar for the month (missing days are not blocked)."""
    first, last = month_bounds(year, month)
    blocked: list[date] = []
    day = first
    while day <= last:
        if day in calendar and not calendar[day]:
            blocked.append(day)
        day += timedelta(days=1)
    return blocked


def format_blocked_ranges_for_month(blocked_days: list[date]) -> str:
    """Day-only labels inside a month: 09–12, 21–24 or 17."""
    if not blocked_days:
        return ""
    ranges = build_date_ranges(blocked_days)
    parts: list[str] = []
    for item in ranges:
        from_d = date.fromisoformat(item["from"])
        to_d = date.fromisoformat(item["to"])
        if from_d == to_d:
            parts.append(f"{from_d.day:02d}")
        else:
            parts.append(f"{from_d.day:02d}–{to_d.day:02d}")
    return ", ".join(parts)


def format_price_thb(price: float) -> str:
    rounded = int(round(price))
    if rounded < 0:
        rounded = 0
    text = f"{rounded:,}".replace(",", " ")
    return f"{text} ฿"


def format_notion_month_cell(
    display_status: MonthDisplayStatus,
    price: float | None = None,
    *,
    blocked_ranges_display: str | None = None,
) -> str:
    """Human-readable rich_text value for a Notion month column."""
    if display_status == MonthDisplayStatus.FULLY_AVAILABLE:
        if blocked_ranges_display:
            raise MonthDisplayConsistencyError(
                f"FULLY_AVAILABLE month cannot have blocked ranges: {blocked_ranges_display}"
            )
        if price is not None:
            return format_price_thb(price)
        return "Доступен"
    if display_status == MonthDisplayStatus.PARTIAL:
        ranges = (blocked_ranges_display or "").strip()
        if ranges:
            if price is not None:
                return f"Частично · {format_price_thb(price)} 🔴 {ranges}"
            return f"Частично 🔴 {ranges}"
        if price is not None:
            return f"Частично · {format_price_thb(price)}"
        return "Частично"
    if display_status == MonthDisplayStatus.UNAVAILABLE:
        return "Недоступен 🔴 весь месяц"
    return "Неизвестно"


def build_month_display_rows(
    window: list[MonthWindowItem],
    calendar: dict[date, bool] | list[CalendarDay],
    monthly_prices: dict[str, float | None] | None = None,
) -> MonthDisplayBuildResult:
    """Return display rows, warnings, and blocked-range labels per month_key."""
    cal_dict = calendar if isinstance(calendar, dict) else {
        day.date: day.available for day in normalize_calendar(calendar)
    }
    prices = monthly_prices or {}
    result = MonthDisplayBuildResult()
    for item in window:
        status = evaluate_month_display_status(cal_dict, item.year, item.month)
        blocked_days = blocked_days_in_month(cal_dict, item.year, item.month)
        blocked_display = format_blocked_ranges_for_month(blocked_days)
        if blocked_display:
            result.blocked_ranges_by_key[item.key] = blocked_display

        if status == MonthDisplayStatus.FULLY_AVAILABLE and blocked_days:
            raise MonthDisplayConsistencyError(
                f"{item.key}: FULLY_AVAILABLE but BLOCKED days {blocked_days}"
            )
        if status == MonthDisplayStatus.PARTIAL and not blocked_days:
            msg = (
                f"{item.key} ({item.display_name}): PARTIAL without BLOCKED days in calendar — "
                "showing fallback without 🔴 ranges"
            )
            result.warnings.append(msg)
            logger.warning(msg)

        price = prices.get(item.key)
        cell = format_notion_month_cell(
            status,
            price,
            blocked_ranges_display=blocked_display or None,
        )
        result.rows.append((item, status, cell))
    return result


def month_display_counts(
    rows: list[tuple[MonthWindowItem, MonthDisplayStatus, str]],
) -> dict[str, int]:
    counts = {
        "fully_available": 0,
        "partial": 0,
        "unavailable": 0,
        "unknown": 0,
    }
    for _, status, _ in rows:
        if status == MonthDisplayStatus.FULLY_AVAILABLE:
            counts["fully_available"] += 1
        elif status == MonthDisplayStatus.PARTIAL:
            counts["partial"] += 1
        elif status == MonthDisplayStatus.UNAVAILABLE:
            counts["unavailable"] += 1
        else:
            counts["unknown"] += 1
    return counts


def calendar_dict_from_days(days: list[CalendarDay]) -> dict[date, bool]:
    return {day.date: day.available for day in days}


def month_availabilities_to_price_map(
    months: list[MonthAvailability],
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for item in months:
        key = f"{item.year:04d}-{item.month:02d}"
        out[key] = item.price
    return out
