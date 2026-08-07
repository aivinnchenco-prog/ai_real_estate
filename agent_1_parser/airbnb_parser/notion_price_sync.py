"""Синхронизация monthly_prices в Notion (опционально, server-side background)."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import date

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
FIELD_MONTHLY = "monthly_prices"
FIELD_PRICE_MONTH = "Цена за месяц"
_CHIP_RE = re.compile(
    r"^(?P<month>\d{4}-\d{2})\s*(?P<sep>[·≈])\s*(?P<price>[\d\s\xa0]+)\s*฿"
)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('NOTION_API_KEY', '')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(url: str, payload: dict | None = None, method: str = "POST") -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def monthly_price_options(monthly: dict) -> list[str]:
    options = []
    for month in sorted(monthly):
        entry = monthly.get(month) or {}
        price = entry.get("price")
        if not price:
            continue
        sep = "≈" if entry.get("status") == "prorated" else "·"
        pretty = f"{int(price):,}".replace(",", " ")
        options.append(f"{month} {sep} {pretty} ฿")
    return options


def first_upcoming_price(monthly: dict, today: date | None = None) -> float | None:
    today = today or date.today()
    current = f"{today.year:04d}-{today.month:02d}"
    for month in sorted(monthly):
        if month <= current:
            continue
        entry = monthly.get(month) or {}
        if entry.get("price"):
            return float(entry["price"])
    return None


def write_prices_to_notion(page_id: str, monthly: dict) -> None:
    options = monthly_price_options(monthly)
    props: dict = {}
    if options:
        props[FIELD_MONTHLY] = {"multi_select": [{"name": name} for name in options]}
    upcoming = first_upcoming_price(monthly)
    if upcoming is not None:
        props[FIELD_PRICE_MONTH] = {"number": upcoming}
    if not props:
        return
    _request(f"{NOTION_API}/pages/{page_id}", {"properties": props}, method="PATCH")
