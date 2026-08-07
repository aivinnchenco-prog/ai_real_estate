"""Статусы и агрегаты monthly pricing (partial / complete)."""

from __future__ import annotations

from datetime import date
from typing import Any

from monthly_pricing import entry_has_price, month_keys_ahead

TERMINAL_JOB_STATUSES = frozenset(
    {"done", "insufficient_data", "currency_mismatch"}
)
ACTIVE_JOB_STATUSES = frozenset({"pending", "running", "retry", "blocked"})


def compute_pricing_status(
    monthly_prices: dict[str, Any],
    *,
    months_target: int,
    has_active_jobs: bool,
    expected_keys: list[str] | None = None,
) -> str:
    """collecting | partial | complete.

    collecting — есть pending/running/retry/blocked jobs;
    partial — сбор завершён, но usable price не для всех target months;
    complete — все target months имеют цену.
    """
    if has_active_jobs:
        return "collecting"

    keys = expected_keys or list(monthly_prices.keys())
    if not keys and months_target:
        return "collecting"

    priced = sum(
        1 for k in keys if entry_has_price((monthly_prices or {}).get(k))
    )
    if priced >= months_target:
        return "complete"

    processed = sum(1 for k in keys if k in (monthly_prices or {}))
    if processed >= months_target or priced > 0:
        return "partial"
    return "collecting"


def pricing_months_collected(monthly_prices: dict[str, Any]) -> int:
    """Месяцы с ценой (для Agent 3 и счётчиков)."""
    return sum(1 for v in (monthly_prices or {}).values() if entry_has_price(v))


def serialize_calendar(availability: dict[date, bool]) -> dict[str, bool]:
    return {d.isoformat(): bool(v) for d, v in (availability or {}).items()}


def deserialize_calendar(raw: dict[str, bool] | None) -> dict[date, bool]:
    out: dict[date, bool] = {}
    for key, val in (raw or {}).items():
        try:
            out[date.fromisoformat(key)] = bool(val)
        except ValueError:
            continue
    return out


def expected_month_keys(months_target: int, today: date | None = None) -> list[str]:
    return month_keys_ahead(today, months_target)
