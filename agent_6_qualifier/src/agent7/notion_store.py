"""Чтение объектов и запись занятости в Notion CRM (общая база Agents 2/6).

Имена свойств — как в живой схеме базы (интеграции работают по именам).
Перед запуском сверить схему: у Agents 2/6 встречалась «Фото » с хвостовым пробелом.
"""
from __future__ import annotations

import os
from datetime import date

import requests

from .models import Availability, Listing

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"

# Notion property -> наше поле
PROP_TITLE = "Название объекта"
PROP_OBJECT_ID = "Объект ID"
PROP_DISTRICT = "Район"
PROP_TYPE = "Тип жилья"
PROP_ROOMS = "Количество комнат"
PROP_PRICE_MONTH = "Цена за месяц"
PROP_PETS = "Можно с питомцами"
PROP_PHOTOS = "Фото"
PROP_TG_POST = "post_url_telegram"
PROP_SOURCE = "Источник объявления"
PROP_CALENDAR = "Календарь"  # URL проверки доступности: Airbnb / календарь УК / «ручной»
PROP_OWNER = "Владелец / Агент"
PROP_OWNER_WA = "WhatsApp контакт"
PROP_OWNER_TG = "Telegram контакт"
# Новые колонки availability (создать в базе, см. AGENT_SPEC.md раздел 6)
PROP_AVAILABILITY = "availability_status"
PROP_BUSY_UNTIL = "Занято до"
PROP_FUTURE_BOOKINGS = "Будущие брони"
PROP_AVAIL_CHECKED = "availability_checked_at"
# Чекбокс для внешнего агента-актуализатора: галочка = доступность подтверждена
PROP_FREE_FLAG = "Свободно"


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
        housing_type=_plain(p.get(PROP_TYPE)),
        rooms=int(float(rooms_raw)) if rooms_raw else None,
        price_month=float(price_raw) if price_raw else None,
        pets_allowed=pets,
        photos_url=photos,
        tg_post_url=_plain(p.get(PROP_TG_POST)),
        source_url=_plain(p.get(PROP_SOURCE)),
        calendar_url=_plain(p.get(PROP_CALENDAR)),
        owner_name=_plain(p.get(PROP_OWNER)),
        owner_whatsapp=_plain(p.get(PROP_OWNER_WA)),
        owner_telegram=_plain(p.get(PROP_OWNER_TG)),
        availability=availability,
        busy_until=date.fromisoformat(busy_raw["start"]) if busy_raw.get("start") else None,
    )


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


def find_by_object_id(object_id: str) -> Listing | None:
    db = os.environ["NOTION_DATABASE_ID"]
    body = {"filter": {"property": PROP_OBJECT_ID, "rich_text": {"equals": object_id}}}
    r = requests.post(f"{_API}/databases/{db}/query", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    pages = r.json()["results"]
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
