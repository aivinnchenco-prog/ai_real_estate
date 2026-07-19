"""Дисковый кэш удачных monthly_prices по Airbnb listing_id.

Повторный парсинг того же объекта не гоняет Airbnb за месяцы, где цена уже есть.
Месяцы без цены (insufficient_data) всегда перезапрашиваются.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import config
from CustomLogger import logger

_LISTING_RE = re.compile(r"/rooms/(\d+)")


def listing_id_from_url(url: str) -> str | None:
    m = _LISTING_RE.search(url or "")
    return m.group(1) if m else None


def _cache_path(listing_id: str) -> Path:
    root = Path(config.PRICE_CACHE_DIR)
    if not root.is_absolute():
        root = Path(__file__).resolve().parent / root
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{listing_id}.json"


def entry_has_price(entry: dict | None) -> bool:
    return bool(entry and entry.get("price"))


def load_price_cache(url: str) -> dict:
    """Возвращает только записи с ценой, если кэш свежий."""
    if not config.PRICE_CACHE_ENABLED:
        return {}
    listing_id = listing_id_from_url(url)
    if not listing_id:
        return {}
    path = _cache_path(listing_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"price cache read failed: {exc}")
        return {}

    saved_at = float(payload.get("saved_at") or 0)
    ttl = max(1, config.PRICE_CACHE_TTL_DAYS) * 86400
    if saved_at and (time.time() - saved_at) > ttl:
        logger.info(f"price cache expired for {listing_id}")
        return {}

    prices = payload.get("monthly_prices") or {}
    kept = {k: v for k, v in prices.items() if entry_has_price(v)}
    if kept:
        logger.info(f"price cache hit {listing_id}: {len(kept)} месяцев с ценой")
    return kept


def save_price_cache(url: str, monthly_prices: dict) -> None:
    if not config.PRICE_CACHE_ENABLED:
        return
    listing_id = listing_id_from_url(url)
    if not listing_id:
        return
    kept = {k: v for k, v in (monthly_prices or {}).items() if entry_has_price(v)}
    if not kept:
        return
    path = _cache_path(listing_id)
    payload = {
        "listing_id": listing_id,
        "saved_at": time.time(),
        "monthly_prices": kept,
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"price cache saved {listing_id}: {len(kept)} месяцев")
    except OSError as exc:
        logger.warning(f"price cache write failed: {exc}")
