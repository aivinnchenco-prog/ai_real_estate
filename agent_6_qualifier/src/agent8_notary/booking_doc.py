"""Agent 8 (Notary): docx-соглашение о бронировании при подтверждении брони.

Данные клиента/объекта → JSON → node scripts/generate_booking_request.js →
data/contracts/{object_id}_{timestamp}.docx. Документ отправляется клиенту
вместе с сообщением «Бронь по вашему варианту … зафиксирована».

Это НЕ договор оплаты: документ фиксирует заявку (даты, объект, порядок
дальнейших шагов); оплата оформляется отдельным договором после показа.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

_QUALIFIER_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _QUALIFIER_ROOT / "scripts" / "generate_booking_request.js"
_OUT_DIR = _QUALIFIER_ROOT / "data" / "contracts"

# Сколько дней действует бронь-заявка до заключения основного договора
RESERVATION_VALID_DAYS = 7


def _agency() -> dict:
    path = _QUALIFIER_ROOT.parent / "config" / "project.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("agency") or {}
    except (OSError, ValueError):
        return {}


def _fmt(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


_TYPE_EN = {
    "вилла": "Villa", "дом": "House", "квартира": "Apartment",
    "апартаменты": "Apartment", "кондоминиум": "Condo", "кондо": "Condo",
    "таунхаус": "Townhouse", "студия": "Studio", "бунгало": "Bungalow",
}


def _property_name(listing) -> str:
    """Пункт 2 договора — без парсерного названия объявления (оно длинное и
    искажается): краткое описание из структурированных данных Notion,
    двуязычно, например «Villa, 3 bedrooms / Вилла, 3 спальни»."""
    if listing is None:
        return "—"
    housing = (listing.housing_type or "").strip()
    rooms = listing.rooms
    ru = housing.capitalize() if housing else "Объект"
    en = _TYPE_EN.get(housing.lower(), ru)
    if rooms:
        n = int(rooms)
        word = ("спальня" if n % 10 == 1 and n % 100 != 11
                else "спальни" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)
                else "спален")
        ru += f", {n} {word}"
        en += f", {n} bedroom" + ("s" if n != 1 else "")
    return f"{en} / {ru}" if en != ru else ru


def build_booking_data(session, client_contact: str = "") -> dict:
    """JSON для generate_booking_request.js из сессии квалификатора.

    client_contact — телефон/username Telegram клиента (из юзербота).
    """
    lead = session.lead
    listing = session.chosen
    object_id = (listing.object_id if listing else lead.preferred_object_id) or "—"
    agency = _agency()
    today = date.today()

    # Дата выезда не названа = годовой контракт (фиксируем это и в договоре)
    long_term = bool(lead.check_in and not lead.check_out)
    check_out = lead.check_out
    if long_term and lead.check_in:
        check_out = lead.check_in + timedelta(days=365)

    if lead.budget:
        budget = f"{lead.budget:,.0f}".replace(",", " ")
    else:
        budget = "—"

    return {
        "contract": {
            "number": f"OH-{object_id}-R{datetime.now():%d%m%H%M}",
            "date": _fmt(today),
            "validUntil": _fmt(today + timedelta(days=RESERVATION_VALID_DAYS)),
        },
        "agency": {
            "brand": agency.get("brand", "OpenHome"),
            "name": agency.get("legal_name", agency.get("brand", "OpenHome")),
            "email": agency.get("email", ""),
            "phone": agency.get("phone", ""),
        },
        "client": {
            "fullName": lead.full_name or lead.name or "—",
            "citizenship": lead.citizenship or "—",
            # Контакт клиента = мессенджер диалога (WhatsApp — только в amoCRM)
            "phone": client_contact or "—",
            "email": "",
        },
        "object": {
            "objectId": object_id,
            "propertyName": _property_name(listing),
            "address": (listing.address if listing else "") or "—",
            "unit": "—",
            "bedrooms": str(listing.rooms) if listing and listing.rooms else "—",
            "area": "—",
        },
        "request": {
            "checkinDate": _fmt(lead.check_in) or "—",
            "checkoutDate": _fmt(check_out) or "—",
            "longTerm": long_term,
            "longTermNote": "annual contract, 12 months",
            "longTermNoteRu": "годовой контракт, 12 месяцев",
            "guests": str(lead.guests) if lead.guests else "—",
            "budget": budget,
            "budgetCurrency": "THB",
            "budgetPeriod": "per month",
            "budgetPeriodRu": "в месяц",
            "pets": bool(lead.pets),
            "petsNote": "",
            "petsNoteRu": "",
        },
    }


def generate_booking_doc(session, client_contact: str = "") -> Path:
    """Генерирует docx и возвращает путь. Бросает исключение при ошибке —
    вызывающий код не должен ронять отправку сообщения из-за документа."""
    data = build_booking_data(session, client_contact)
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    object_id = data["object"]["objectId"].replace("/", "_")
    out = _OUT_DIR / f"Бронь_{object_id}_{datetime.now():%Y%m%d_%H%M%S}.docx"
    tmp_json = _OUT_DIR / f".{out.stem}.json"
    tmp_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        proc = subprocess.run(
            ["node", str(_SCRIPT), str(tmp_json), str(out)],
            cwd=str(_QUALIFIER_ROOT), capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"generate_booking_request.js: code={proc.returncode}, "
                f"{(proc.stderr or proc.stdout).strip()[:400]}"
            )
    finally:
        tmp_json.unlink(missing_ok=True)
    return out
