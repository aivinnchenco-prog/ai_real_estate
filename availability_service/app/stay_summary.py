"""Deterministic human-readable stay availability summaries (no LLM)."""
from __future__ import annotations

from datetime import date, timedelta

from .date_ranges import build_date_ranges
from .models import AvailabilityStatus, StayAvailabilityResult


def _format_range_ru(from_d: date, to_d: date) -> str:
    months_ru = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    if from_d == to_d:
        return f"{from_d.day} {months_ru[from_d.month - 1]}"
    if from_d.month == to_d.month and from_d.year == to_d.year:
        return f"{from_d.day}–{to_d.day} {months_ru[from_d.month - 1]}"
    left = f"{from_d.day} {months_ru[from_d.month - 1]}"
    right = f"{to_d.day} {months_ru[to_d.month - 1]}"
    return f"{left} и {right}"


def _format_range_en(from_d: date, to_d: date) -> str:
    months_en = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    if from_d == to_d:
        return f"{months_en[from_d.month - 1]} {from_d.day}"
    if from_d.month == to_d.month and from_d.year == to_d.year:
        return f"{from_d.day}–{to_d.day} {months_en[from_d.month - 1]}"
    left = f"{from_d.day} {months_en[from_d.month - 1]}"
    right = f"{to_d.day} {months_en[to_d.month - 1]}"
    return f"{left} and {right}"


def _join_ranges_ru(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return " и ".join(parts)


def _join_ranges_en(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return " and ".join(parts)


def _ranges_to_text(ranges: list[dict[str, str]], locale: str) -> str:
    parts: list[str] = []
    for item in ranges:
        from_d = date.fromisoformat(item["from"])
        to_d = date.fromisoformat(item["to"])
        if locale == "en":
            parts.append(_format_range_en(from_d, to_d))
        else:
            parts.append(_format_range_ru(from_d, to_d))
    if locale == "en":
        return _join_ranges_en(parts)
    return _join_ranges_ru(parts)


def _period_text_ru(check_in: date, check_out: date) -> str:
    months_ru = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    end_day = check_out - timedelta(days=1)
    return (
        f"с {check_in.day} {months_ru[check_in.month - 1]} "
        f"по {end_day.day} {months_ru[end_day.month - 1]}"
    )


def _period_text_en(check_in: date, check_out: date) -> str:
    months_en = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    end_day = check_out - timedelta(days=1)
    return (
        f"from {months_en[check_in.month - 1]} {check_in.day} "
        f"to {months_en[end_day.month - 1]} {end_day.day}"
    )


def format_stay_availability_summary(
    result: StayAvailabilityResult,
    locale: str = "ru",
) -> str:
    """Format stay check result for humans without LLM."""
    locale = (locale or "ru").strip().lower()
    if result.status == AvailabilityStatus.AVAILABLE:
        if locale == "en":
            return (
                f"The property is available for the entire period "
                f"{_period_text_en(result.check_in, result.check_out)}."
            )
        return (
            f"Объект свободен на весь период {_period_text_ru(result.check_in, result.check_out)}."
        )

    if result.status == AvailabilityStatus.UNAVAILABLE:
        blocked_text = _ranges_to_text(result.blocked_ranges, locale)
        if locale == "en":
            return (
                f"The property has blocked dates during the selected period: {blocked_text}."
            )
        return f"На выбранный период есть занятые даты: {blocked_text}."

    unknown_ranges = result.unknown_ranges
    if not unknown_ranges and result.unknown_dates:
        unknown_ranges = build_date_ranges(result.unknown_dates)
    unknown_text = _ranges_to_text(unknown_ranges, locale)
    if locale == "en":
        return (
            f"Some dates in the selected period lack current availability data: {unknown_text}."
        )
    return f"По части выбранного периода нет актуальных данных: {unknown_text}."
