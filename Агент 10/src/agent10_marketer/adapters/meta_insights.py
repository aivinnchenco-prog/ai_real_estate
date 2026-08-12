"""Normalize Meta Insights payloads → PaidPerformanceMetrics.

Missing fields stay None. Explicit zeros stay 0. Never invent values.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from agent10_marketer.models import PaidPerformanceMetrics

# Canonical Meta Insights fields Agent 10 understands today.
CANONICAL_INSIGHT_FIELDS: tuple[str, ...] = (
    "spend",
    "impressions",
    "reach",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
)

# Future / optional business outcomes (not invented from Meta Insights alone).
FUTURE_INSIGHT_FIELDS: tuple[str, ...] = (
    "leads",
    "cpl",
    "qualified_leads",
    "cost_per_qualified_lead",
    "bookings",
    "cost_per_booking",
)


def parse_meta_number(raw: Any) -> float | None:
    """Parse Meta numeric string/number. Empty/absent → None. '0' → 0.0."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if text == "":
        return None
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def parse_meta_money(raw: Any) -> Decimal | None:
    """Parse money-like Meta values as Decimal (preferred for budgets/spend)."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    text = str(raw).strip()
    if text == "":
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _first_row(payload: dict[str, Any] | list[Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], dict) else {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    # Already a flat insights row
    if any(k in payload for k in CANONICAL_INSIGHT_FIELDS):
        return payload
    return {}


def normalize_insights(payload: dict[str, Any] | list[Any] | None) -> PaidPerformanceMetrics:
    """Map raw Meta Insights response (or row) to PaidPerformanceMetrics."""
    row = _first_row(payload)
    return PaidPerformanceMetrics(
        spend=parse_meta_number(row.get("spend")),
        impressions=parse_meta_number(row.get("impressions")),
        reach=parse_meta_number(row.get("reach")),
        clicks=parse_meta_number(row.get("clicks")),
        ctr=parse_meta_number(row.get("ctr")),
        cpc=parse_meta_number(row.get("cpc")),
        cpm=parse_meta_number(row.get("cpm")),
        # Future fields: only if Meta (or upstream) actually returned them.
        leads=parse_meta_number(row.get("leads")),
        cpl=parse_meta_number(row.get("cpl") or row.get("cost_per_lead")),
        qualified_leads=parse_meta_number(row.get("qualified_leads")),
        cost_per_qualified_lead=parse_meta_number(row.get("cost_per_qualified_lead")),
        bookings=parse_meta_number(row.get("bookings")),
        cost_per_booking=parse_meta_number(row.get("cost_per_booking")),
    )


def normalize_account(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize GET /act_{id} account fields."""
    raw = payload or {}
    return {
        "id": str(raw["id"]) if raw.get("id") is not None else None,
        "name": str(raw["name"]) if raw.get("name") is not None else None,
        "account_status": raw.get("account_status"),
        "currency": str(raw["currency"]) if raw.get("currency") is not None else None,
        "timezone_name": (
            str(raw["timezone_name"]) if raw.get("timezone_name") is not None else None
        ),
    }


def normalize_campaign(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]) if row.get("id") is not None else None,
        "name": str(row["name"]) if row.get("name") is not None else None,
        "status": str(row["status"]) if row.get("status") is not None else None,
        "effective_status": (
            str(row["effective_status"]) if row.get("effective_status") is not None else None
        ),
    }
