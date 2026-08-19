"""Оркестрация: primary month (blocking) + enqueue background months."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

import config
from agent2_handoff import find_agent2_root
from availability import fetch_calendar_days
from CustomLogger import logger
from monthly_pricing import (
    background_month_keys,
    entry_has_price,
    month_keys_ahead,
    price_for_month,
    primary_month_key,
)
from price_cache import load_price_cache, save_price_cache
from pricing_config import months_ahead
from pricing_gate import run_pricing
from pricing_queue import get_queue
from pricing_state import (
    compute_pricing_status,
    deserialize_calendar,
    expected_month_keys as build_expected_month_keys,
    pricing_months_collected,
    serialize_calendar,
)
from pricing_worker import ensure_background_worker


def _seed_from_listing_price(listing_data: dict) -> dict:
    from datetime import date as _date

    from monthly_pricing import round_to_hundreds

    raw = listing_data.get("Цена") or ""
    try:
        value = float(str(raw).replace(" ", "").replace("\xa0", "").replace(",", ""))
    except (TypeError, ValueError):
        return {}
    if value <= 0:
        return {}
    check_in = (listing_data.get("Дата_заезд") or "").strip()
    check_out = (listing_data.get("Дата_выезд") or "").strip()
    if not check_in:
        return {}
    try:
        start = _date.fromisoformat(check_in[:10])
    except ValueError:
        return {}
    key = f"{start.year:04d}-{start.month:02d}"
    period = f"{check_in}/{check_out}" if check_out else check_in
    return {
        key: {
            "price": round_to_hundreds(value),
            "status": "monthly",
            "period_used": period,
            "source": "listing_parse",
        }
    }


def _merge_existing(url: str, listing_data: dict | None) -> dict:
    cached = load_price_cache(url)
    seeded = _seed_from_listing_price(listing_data or {})
    merged = dict(cached or {})
    for key, entry in seeded.items():
        if not entry_has_price(merged.get(key)):
            merged[key] = entry
    return merged


def _parse_month(month_key: str) -> tuple[int, int]:
    y, m = month_key.split("-")
    return int(y), int(m)


def _fetch_once(parser, url: str, state: dict) -> Callable:
    """Один сетевой запрос на вызов price_for_month (без inline refill)."""

    def fetch(check_in, check_out):
        value, blocked = parser._fetch_price_for_period_once(url, check_in, check_out)
        if blocked:
            state["blocked"] = True
        if value is None and not blocked:
            display_currency = getattr(parser, "_last_price_currency", None)
            target = getattr(parser, "_target_currency", None)
            if display_currency and target and display_currency != target:
                state["currency_mismatch"] = display_currency
        return value

    return fetch


def collect_primary_month(
    parser,
    url: str,
    listing_data: dict | None,
    timings: dict | None = None,
    *,
    today: date | None = None,
) -> tuple[dict[str, dict], dict[date, bool]]:
    """Блокирующий сбор ближайшего месяца + один calendar snapshot (global gate)."""
    timings = timings if timings is not None else {}
    target_months = months_ahead()
    today = today or date.today()

    def _work() -> tuple[dict[str, dict], dict[date, bool]]:
        t0 = time.perf_counter()
        availability = fetch_calendar_days(url)
        timings["calendar_sec"] = round(time.perf_counter() - t0, 1)

        existing = _merge_existing(url, listing_data)
        primary = primary_month_key(today, target_months)
        result = dict(existing)

        if not primary:
            return result, availability

        if entry_has_price(existing.get(primary)):
            logger.info(f"primary month {primary} из кэша")
            entry = dict(existing[primary])
            entry["queue_status"] = "priority"
            result[primary] = entry
            return result, availability

        year, month = _parse_month(primary)
        fetch_state: dict[str, Any] = {}
        t0 = time.perf_counter()
        entry = price_for_month(
            _fetch_once(parser, url, fetch_state),
            availability,
            year,
            month,
            min_segment_days=config.PRICE_MIN_SEGMENT_DAYS,
        )
        timings["primary_price_sec"] = round(time.perf_counter() - t0, 1)

        if fetch_state.get("currency_mismatch"):
            entry = {
                "price": None,
                "status": "currency_mismatch",
                "raw_currency": fetch_state["currency_mismatch"],
            }
        elif fetch_state.get("blocked"):
            entry["queue_status"] = "priority"
            entry["blocked"] = True
        else:
            entry["queue_status"] = "priority"

        result[primary] = entry
        save_price_cache(url, result)
        return result, availability

    return run_pricing(_work, primary=True)


def schedule_background_pricing(
    *,
    object_id: str,
    listing_url: str,
    monthly_prices: dict,
    calendar: dict[date, bool] | None,
    session_id: str = "",
    notion_page_id: str = "",
    today: date | None = None,
) -> int:
    """Ставит в очередь оставшиеся месяцы; запускает background worker."""
    if not object_id or not getattr(config, "PRICE_COLLECT_ENABLED", True):
        return 0

    target = months_ahead()
    today = today or date.today()
    bg_months = background_month_keys(today, target)
    if not bg_months:
        return 0

    queue = get_queue()
    obj_seq = queue.next_object_seq()
    expected = month_keys_ahead(today, target)
    queue.upsert_object(
        object_id,
        listing_url=listing_url,
        monthly_prices=monthly_prices,
        calendar=serialize_calendar(calendar or {}),
        session_id=session_id,
        notion_page_id=notion_page_id,
        months_target=target,
        object_seq=obj_seq,
        expected_month_keys=expected,
    )
    created = queue.enqueue_background_months(
        object_id,
        listing_url,
        bg_months,
        existing=monthly_prices,
        object_seq=obj_seq,
    )
    queue.save()
    if created:
        logger.info(
            f"pricing queue: {object_id} +{len(created)} background jobs "
            f"(target={target})"
        )
        ensure_background_worker()
    return len(created)


def resolve_notion_page_id(session_id: str) -> str:
    if not session_id:
        return ""
    root = find_agent2_root()
    if not root:
        return ""
    path = root / "data" / "sessions" / session_id / "session.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data.get("notion_page_id") or "").strip()
    except (OSError, ValueError):
        return ""


def register_after_handoff(
    *,
    object_id: str,
    session_id: str,
    listing_url: str,
    monthly_prices: dict,
    calendar: dict[date, bool] | None,
) -> int:
    page_id = resolve_notion_page_id(session_id)
    return schedule_background_pricing(
        object_id=object_id,
        listing_url=listing_url,
        monthly_prices=monthly_prices,
        calendar=calendar,
        session_id=session_id,
        notion_page_id=page_id,
    )


def object_pricing_summary(object_id: str) -> dict[str, Any]:
    queue = get_queue()
    obj = queue.get_object(object_id) or {}
    monthly = obj.get("monthly_prices") or {}
    target = int(obj.get("pricing_months_target") or months_ahead())
    active = queue.has_active_jobs(object_id)
    expected = obj.get("expected_month_keys") or build_expected_month_keys(target)
    return {
        "pricing_status": compute_pricing_status(
            monthly,
            months_target=target,
            has_active_jobs=active,
            expected_keys=expected,
        ),
        "pricing_months_target": target,
        "pricing_months_collected": pricing_months_collected(monthly),
        "monthly_prices": monthly,
    }
