"""Чтение объектов и запись занятости в Notion CRM (общая база Agents 2/6).

Имена свойств — как в живой схеме базы (интеграции работают по именам).
Перед запуском сверить схему: у Agents 2/6 встречалась «Фото » с хвостовым пробелом.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import requests

from .models import Availability, Listing

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"

# Имена колонок Notion. Дефолты — живая схема базы; при переименовании
# колонок достаточно переопределить нужные в config/notion_fields.json
# (ключи как в _DEFAULT_FIELDS), код не трогать.
_DEFAULT_FIELDS = {
    "title": "Название объекта",
    "object_id": "Объект ID",
    "district": "Район",
    "address": "Адрес",
    "type": "Тип жилья",
    "rooms": "Количество комнат",
    "price_month": "Цена за месяц",
    "pets": "Можно с питомцами",
    "photos": "Фото",
    "tg_post": "post_url_telegram",
    "google_maps": "Google Maps",
    "source": "Источник объявления",
    # URL проверки доступности: Airbnb / календарь УК / «ручной»
    "calendar": "Календарь",
    "owner": "Владелец / Агент",
    "owner_wa": "WhatsApp контакт",
    "owner_tg": "Telegram контакт",
    "availability": "availability_status",
    "busy_until": "Занято до",
    "future_bookings": "Будущие брони",
    "avail_checked": "availability_checked_at",
    # Чекбокс для внешнего агента-актуализатора: галочка = доступность подтверждена
    "free_flag": "Свободно",
    # JSON цен по месяцам от Агента 1/2: {"2026-09": {"price": ..., "status": ...}}
    "monthly_prices": "monthly_prices",
}


def _load_fields() -> dict:
    override = Path(__file__).resolve().parents[2] / "config" / "notion_fields.json"
    fields = dict(_DEFAULT_FIELDS)
    if override.exists():
        try:
            fields.update(json.loads(override.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return fields


_F = _load_fields()

PROP_TITLE = _F["title"]
PROP_OBJECT_ID = _F["object_id"]
PROP_DISTRICT = _F["district"]
PROP_ADDRESS = _F["address"]
PROP_TYPE = _F["type"]
PROP_ROOMS = _F["rooms"]
PROP_PRICE_MONTH = _F["price_month"]
PROP_PETS = _F["pets"]
PROP_PHOTOS = _F["photos"]
PROP_TG_POST = _F["tg_post"]
PROP_GMAPS = _F["google_maps"]
PROP_SOURCE = _F["source"]
PROP_CALENDAR = _F["calendar"]
PROP_OWNER = _F["owner"]
PROP_OWNER_WA = _F["owner_wa"]
PROP_OWNER_TG = _F["owner_tg"]
PROP_AVAILABILITY = _F["availability"]
PROP_BUSY_UNTIL = _F["busy_until"]
PROP_FUTURE_BOOKINGS = _F["future_bookings"]
PROP_AVAIL_CHECKED = _F["avail_checked"]
PROP_FREE_FLAG = _F["free_flag"]
PROP_MONTHLY_PRICES = _F["monthly_prices"]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": _VERSION,
        "Content-Type": "application/json",
    }


def _plain(prop: dict | None) -> str:
    """Достаёт текст из rich_text/title/url/select/status/number."""
    if not prop:
        return ""
    t = prop.get("type")
    if t in ("rich_text", "title"):
        return "".join(x.get("plain_text", "") for x in prop.get(t, []))
    if t == "url":
        return prop.get("url") or ""
    if t in ("select", "status"):
        v = prop.get(t)
        return (v or {}).get("name", "")
    if t == "number":
        n = prop.get("number")
        return "" if n is None else str(n)
    if t == "checkbox":
        return "true" if prop.get("checkbox") else ""
    return ""


# Плашка Агента 2: «2026-09 · 99 200 ฿» (точная) / «2026-12 ≈ 249 500 ฿»
# (экстраполирована с части месяца → статус prorated).
_MONTHLY_CHIP_RE = re.compile(r"^(\d{4}-\d{2})\s*([·≈~])\s*([\d\s\u00a0]+)")


def _parse_monthly_prices(prop: dict | None) -> dict:
    """monthly_prices: multi_select-плашки (текущий формат) или JSON (легаси)."""
    if not prop:
        return {}
    result: dict = {}
    if prop.get("type") == "multi_select":
        for opt in prop.get("multi_select", []):
            m = _MONTHLY_CHIP_RE.match((opt.get("name") or "").strip())
            if not m:
                continue
            digits = re.sub(r"\D", "", m.group(3))
            if not digits:
                continue
            status = "monthly" if m.group(2) == "·" else "prorated"
            result[m.group(1)] = {"price": int(digits), "status": status}
        return result
    raw = _plain(prop).strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                result = parsed
        except ValueError:
            pass
    return result


def _to_listing(page: dict) -> Listing:
    p = page.get("properties", {})
    photos = _plain(p.get(PROP_PHOTOS)) or _plain(p.get(PROP_PHOTOS + " "))
    pets_prop = p.get(PROP_PETS) or {}
    pets: bool | None
    if pets_prop.get("type") == "checkbox":
        # Чекбокс в живой схеме: галочка = можно; снятая = «не указано»
        # (не «нельзя»!), такие объекты тоже предлагаем клиентам с животными.
        pets = True if pets_prop.get("checkbox") else None
    else:
        pets_raw = _plain(pets_prop).strip().lower()
        pets = None
        if pets_raw in ("да", "yes", "true", "можно"):
            pets = True
        elif pets_raw in ("нет", "no", "false", "нельзя"):
            pets = False
    rooms_raw = _plain(p.get(PROP_ROOMS))
    price_raw = _plain(p.get(PROP_PRICE_MONTH))
    monthly_prices = _parse_monthly_prices(p.get(PROP_MONTHLY_PRICES))
    avail_raw = _plain(p.get(PROP_AVAILABILITY))
    busy_raw = (p.get(PROP_BUSY_UNTIL) or {}).get("date") or {}

    availability = (
        Availability(avail_raw)
        if avail_raw in [a.value for a in Availability]
        else Availability.UNKNOWN
    )
    # Галочка «Свободно» от агента-актуализатора подтверждает доступность,
    # если статус не говорит явно «занято». Снятая галочка = нет информации.
    if (p.get(PROP_FREE_FLAG) or {}).get("checkbox") and availability != Availability.BUSY:
        availability = Availability.FREE

    return Listing(
        object_id=_plain(p.get(PROP_OBJECT_ID)),
        page_id=page.get("id", ""),
        title=_plain(p.get(PROP_TITLE)),
        district=_plain(p.get(PROP_DISTRICT)),
        address=_plain(p.get(PROP_ADDRESS)),
        housing_type=_plain(p.get(PROP_TYPE)),
        rooms=int(float(rooms_raw)) if rooms_raw else None,
        price_month=float(price_raw) if price_raw else None,
        pets_allowed=pets,
        photos_url=photos,
        tg_post_url=_plain(p.get(PROP_TG_POST)),
        google_maps=_plain(p.get(PROP_GMAPS)),
        source_url=_plain(p.get(PROP_SOURCE)),
        calendar_url=_plain(p.get(PROP_CALENDAR)),
        owner_name=_plain(p.get(PROP_OWNER)),
        owner_whatsapp=_plain(p.get(PROP_OWNER_WA)),
        owner_telegram=_plain(p.get(PROP_OWNER_TG)),
        availability=availability,
        busy_until=date.fromisoformat(busy_raw["start"]) if busy_raw.get("start") else None,
        monthly_prices=monthly_prices,
    )


def fetch_all_pages() -> list[dict]:
    """Raw Notion pages for publication index building."""
    db = os.environ["NOTION_DATABASE_ID"]
    results, cursor = [], None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"{_API}/databases/{db}/query", headers=_headers(), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        results.extend(data["results"])
        if not data.get("has_more"):
            return results
        cursor = data["next_cursor"]


def fetch_all_listings() -> list[Listing]:
    db = os.environ["NOTION_DATABASE_ID"]
    results, cursor = [], None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"{_API}/databases/{db}/query", headers=_headers(), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        results += [_to_listing(pg) for pg in data["results"]]
        if not data.get("has_more"):
            return results
        cursor = data["next_cursor"]


def find_by_tg_post(channel: str, message_id: int) -> Listing | None:
    """Match post_url_telegram against t.me/channel/message_id."""
    target = f"https://t.me/{channel.lstrip('@')}/{message_id}"
    for page in fetch_all_pages():
        url = _plain((page.get("properties") or {}).get(PROP_TG_POST))
        if url and url.rstrip("/") == target.rstrip("/"):
            return _to_listing(page)
    return None


def find_by_object_id(object_id: str) -> Listing | None:
    """Поиск по «Объект ID». Клиент может опустить префикс источника
    (написать 20260713_003 вместо A_20260713_003) — тогда ищем по вхождению
    и принимаем страницу, чей ID заканчивается на присланный номер."""
    db = os.environ["NOTION_DATABASE_ID"]

    def query(condition: dict) -> list[dict]:
        body = {"filter": {"property": PROP_OBJECT_ID, "rich_text": condition}}
        r = requests.post(f"{_API}/databases/{db}/query",
                          headers=_headers(), json=body, timeout=30)
        r.raise_for_status()
        return r.json()["results"]

    pages = query({"equals": object_id})
    if not pages:
        pages = [p for p in query({"contains": object_id})
                 if _to_listing(p).object_id.endswith(object_id)]
    return _to_listing(pages[0]) if pages else None


def update_availability(
    page_id: str,
    status: Availability,
    busy_until: date | None = None,
    future_bookings: str = "",
) -> None:
    """Agent 8 пишет сюда всё, что узнал от владельца / из календаря Airbnb.

    Чекбокс «Свободно» держим в согласии со статусом: подтверждённое
    «свободно» ставит галочку, «занято»/«уточняется» — снимает.
    """
    props: dict = {
        PROP_AVAILABILITY: {"select": {"name": status.value}},
        PROP_AVAIL_CHECKED: {"date": {"start": date.today().isoformat()}},
        PROP_FREE_FLAG: {"checkbox": status == Availability.FREE},
    }
    if busy_until:
        props[PROP_BUSY_UNTIL] = {"date": {"start": busy_until.isoformat()}}
    if future_bookings:
        props[PROP_FUTURE_BOOKINGS] = {"rich_text": [{"text": {"content": future_bookings[:1900]}}]}
    r = requests.patch(f"{_API}/pages/{page_id}", headers=_headers(), json={"properties": props}, timeout=30)
    r.raise_for_status()


def save_owner_whatsapp(page_id: str, whatsapp: str) -> None:
    """Номер WA, полученный в FB Marketplace, сразу в Notion (и параллельно в amo)."""
    props = {PROP_OWNER_WA: {"rich_text": [{"text": {"content": whatsapp}}]}}
    r = requests.patch(f"{_API}/pages/{page_id}", headers=_headers(), json={"properties": props}, timeout=30)
    r.raise_for_status()


def save_calendar_url(page_id: str, url: str) -> None:
    """Ссылка на календарь объекта (Airbnb / iCal / GCal / Google Sheets УК),
    полученная от владельца в переписке, — в колонку «Календарь»."""
    r = requests.patch(f"{_API}/pages/{page_id}", headers=_headers(),
                       json={"properties": {PROP_CALENDAR: {"url": url}}}, timeout=30)
    r.raise_for_status()
