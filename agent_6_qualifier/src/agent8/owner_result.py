"""Разбор ответа владельца и сообщение клиенту (замыкание цикла Agent 8 → Agent 7)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date

from agent7.brain import _call, _text
from agent7.models import Availability
from agent7.qualifier import Session
from agent7.templates import (
    client_object_busy,
    client_owner_conditions,
    client_owner_confirmed,
)

OWNER_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "status": {
            "type": "STRING",
            "description": "free = свободно на даты клиента; busy = занято; conditions_changed = согласен, но другие даты/цена",
        },
        "busy_until": {"type": "STRING", "description": "до какого числа занято, ISO YYYY-MM-DD"},
        "future_bookings": {"type": "STRING", "description": "будущие брони, если владелец назвал"},
        "new_price_month": {"type": "NUMBER", "description": "новая цена в месяц THB, если изменилась"},
        "conditions_note": {"type": "STRING", "description": "кратко что изменилось для клиента"},
    },
    "required": ["status"],
}

_OWNER_PROMPT = """Ты разбираешь ответ собственника жилья на запрос о доступности.
Сегодня {today}.

Объект: {object_title} (ID {object_id})
Даты клиента: {check_in} — {check_out}
Гостей: {guests}

Ответ владельца:
{reply}

Верни JSON. status:
- free — даты клиента подтверждены без изменений
- busy — занято на эти даты
- conditions_changed — согласен, но другая цена и/или сдвиг дат
Если владелец не назвал срок занятости при busy — busy_until не включай."""


@dataclass
class OwnerVerdict:
    status: str  # free | busy | conditions_changed
    busy_until: date | None = None
    future_bookings: str = ""
    new_price_month: float | None = None
    conditions_note: str = ""


def parse_owner_reply(reply: str, session: Session) -> OwnerVerdict:
    lead, chosen = session.lead, session.chosen
    payload = {
        "contents": [{"parts": [{"text": _OWNER_PROMPT.format(
            today=date.today().isoformat(),
            object_title=(chosen.title if chosen else lead.preferred_object_id),
            object_id=lead.preferred_object_id,
            check_in=lead.check_in.isoformat() if lead.check_in else "?",
            check_out=lead.check_out.isoformat() if lead.check_out else "?",
            guests=lead.guests or "?",
            reply=reply,
        )}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": OWNER_SCHEMA,
            "temperature": 0.1,
        },
    }
    data = json.loads(_text(_call(payload)))
    busy = None
    if data.get("busy_until"):
        try:
            busy = date.fromisoformat(data["busy_until"])
        except ValueError:
            pass
    return OwnerVerdict(
        status=data.get("status", "busy"),
        busy_until=busy,
        future_bookings=data.get("future_bookings", ""),
        new_price_month=float(data["new_price_month"]) if data.get("new_price_month") else None,
        conditions_note=data.get("conditions_note", ""),
    )


def _date_range(session: Session) -> str:
    lead = session.lead
    if lead.check_in and lead.check_out:
        return f"{lead.check_in.strftime('%d.%m')}–{lead.check_out.strftime('%d.%m.%Y')}"
    if lead.check_in:
        return lead.check_in.strftime("%d.%m.%Y")
    return ""


def build_client_message(verdict: OwnerVerdict, session: Session) -> str:
    # В сообщениях клиенту используем метку объекта («ваш вариант A_..._003»),
    # а не длинный парсерный заголовок.
    object_id = (session.chosen.object_id if session.chosen
                 else session.lead.preferred_object_id)
    if verdict.status == "free":
        return client_owner_confirmed(object_id, _date_range(session))
    if verdict.status == "conditions_changed":
        note = verdict.conditions_note
        if verdict.new_price_month:
            note = (note + " " if note else "") + f"Цена: {verdict.new_price_month:,.0f} THB/мес".replace(",", " ")
        return client_owner_conditions(object_id, note or "уточните детали у менеджера")
    # busy
    if verdict.busy_until:
        from datetime import timedelta
        free_from = verdict.busy_until + timedelta(days=1)
        return client_object_busy(
            object_id,
            verdict.busy_until.strftime("%d.%m.%Y"),
            free_from.strftime("%d.%m.%Y"),
        )
    return (
        f"К сожалению, владелец сообщил, что ваш вариант {object_id} занят "
        "на ваши даты. Подобрать похожие варианты?"
    )


def notion_availability_update(verdict: OwnerVerdict) -> dict:
    if verdict.status == "free":
        return {"status": Availability.FREE, "busy_until": None, "future_bookings": ""}
    if verdict.status == "busy":
        return {
            "status": Availability.BUSY,
            "busy_until": verdict.busy_until,
            "future_bookings": verdict.future_bookings,
        }
    return {"status": Availability.UNKNOWN, "busy_until": None, "future_bookings": verdict.conditions_note}


def apply_verdict_to_session(session: Session, verdict: OwnerVerdict) -> None:
    session.awaiting_owner = False
    session.owner_verdict = verdict.status
    if session.chosen and verdict.status == "busy" and verdict.busy_until:
        session.chosen.availability = Availability.BUSY
        session.chosen.busy_until = verdict.busy_until
    if session.chosen and verdict.status == "free":
        session.chosen.availability = Availability.FREE


def apply_verdict_to_pending(session: Session, object_id: str, verdict: OwnerVerdict) -> bool:
    """Apply owner verdict to a background pending request without touching active search."""
    from agent7.search_context import apply_pending_verdict, find_pending_owner_request

    item = find_pending_owner_request(session, object_id)
    if item is None:
        return False
    apply_pending_verdict(session, object_id, verdict.status)
    chosen = item.get("chosen") or {}
    if chosen and verdict.status == "busy" and verdict.busy_until:
        chosen["availability"] = Availability.BUSY.value
        chosen["busy_until"] = verdict.busy_until.isoformat()
    return True


def build_client_message_for_object(
    verdict: OwnerVerdict,
    object_id: str,
    check_in: date | None = None,
    check_out: date | None = None,
) -> str:
    """Client message for a specific object (active or background request)."""
    if verdict.status == "free":
        dr = ""
        if check_in and check_out:
            dr = f"{check_in.strftime('%d.%m')}–{check_out.strftime('%d.%m.%Y')}"
        elif check_in:
            dr = check_in.strftime("%d.%m.%Y")
        return client_owner_confirmed(object_id, dr)
    if verdict.status == "conditions_changed":
        note = verdict.conditions_note
        if verdict.new_price_month:
            note = (note + " " if note else "") + (
                f"Цена: {verdict.new_price_month:,.0f} THB/мес".replace(",", " ")
            )
        return client_owner_conditions(object_id, note or "уточните детали у менеджера")
    if verdict.busy_until:
        from datetime import timedelta
        free_from = verdict.busy_until + timedelta(days=1)
        return client_object_busy(
            object_id,
            verdict.busy_until.strftime("%d.%m.%Y"),
            free_from.strftime("%d.%m.%Y"),
        )
    return (
        f"К сожалению, владелец сообщил, что ваш вариант {object_id} занят "
        "на ваши даты. Подобрать похожие варианты?"
    )
