"""Agent 7 Envoy — Owner Outreach: связь с собственником и проверка доступности.

Оркестрация одного запроса «проверить объект X на даты клиента»:
1. Pre-check календаря ДО письма владельцу: колонка «Календарь» в Notion
   (Airbnb-ссылка или календарь управляющей компании); пусто/«ручной» — без проверки.
2. Выбор канала по приоритету WA → TG → Airbnb DM → FB Marketplace DM.
3. Первое сообщение по скрипту канала (Airbnb — особый скрипт, без упоминания клиента).
4. Ответ владельца «занято» → уточнить до какого числа / будущие брони → записать в Notion.
5. WA-номер из FB Marketplace → сразу в Notion + amo, дальше общение в WA.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from agent7.airbnb_check import CalendarCheck
from agent7.models import Availability, LeadProfile, Listing, OwnerChannel
from agent7.templates import OWNER_AIRBNB_STEP2, OWNER_BUSY_FOLLOWUP, owner_first_message

from .calendar_check import check_calendar_dates

# Тип функции проверки календаря — подменяется в тестах / на Clawbot.
CalendarChecker = Callable[[str, date, date], CalendarCheck]


@dataclass
class OutreachPlan:
    """Что Agent 7 Envoy собирается сделать по объекту. Пустой channel => владельцу не пишем."""
    listing: Listing
    channel: OwnerChannel | None = None
    contact: str = ""
    first_message: str = ""
    skip_reason: str = ""              # например «календарь Airbnb: даты закрыты»
    precheck: CalendarCheck | None = None


def build_outreach_plan(
    listing: Listing,
    lead: LeadProfile,
    checker: CalendarChecker = check_calendar_dates,
) -> OutreachPlan:
    """Готовит контакт с владельцем. Airbnb pre-check выполняется до выбора канала."""
    plan = OutreachPlan(listing=listing)

    calendar = listing.availability_calendar
    if calendar and lead.check_in and lead.check_out:
        try:
            plan.precheck = checker(calendar, lead.check_in, lead.check_out)
        except Exception:
            # Проверка недоступна (не реализована / сеть / формат) —
            # это не блокер: пишем владельцу как обычно.
            plan.precheck = None
        if plan.precheck and plan.precheck.available is False:
            plan.skip_reason = "календарь: даты клиента закрыты"
            return plan

    ch = listing.owner_channel()
    if ch is None:
        plan.skip_reason = "в Notion нет ни одного контакта владельца"
        return plan

    plan.channel, plan.contact = ch
    plan.first_message = owner_first_message(
        plan.channel,
        check_in=lead.check_in.strftime("%d.%m.%Y") if lead.check_in else "",
        check_out=lead.check_out.strftime("%d.%m.%Y") if lead.check_out else "",
        guests=lead.guests,
        listing_url=listing.source_url,
    )
    return plan


def precheck_alternatives(
    alternatives: list[Listing],
    lead: LeadProfile,
    checker: CalendarChecker = check_calendar_dates,
    on_result: Callable[[Listing, CalendarCheck], None] | None = None,
) -> list[Listing]:
    """Отсев альтернатив с источником Airbnb, у которых даты клиента закрыты.

    Вызывается ПЕРЕД показом альтернатив клиенту / письмом владельцу.
    on_result — колбэк для записи результата в Notion (update_availability).
    Объекты без Airbnb-источника и объекты с недоступной проверкой проходят как есть.
    """
    if not (lead.check_in and lead.check_out):
        return alternatives

    passed: list[Listing] = []
    for listing in alternatives:
        calendar = listing.availability_calendar
        if not calendar:
            passed.append(listing)
            continue
        try:
            result = checker(calendar, lead.check_in, lead.check_out)
        except Exception:
            passed.append(listing)  # сбой проверки не отсеивает объект
            continue
        if on_result:
            on_result(listing, result)
        if result.available is not False:
            passed.append(listing)
    return passed


@dataclass
class OwnerBusyInfo:
    """Разобранный ответ владельца «занято» (парсит Gemini, структура — эта)."""
    busy_until: date | None = None
    future_bookings: str = ""

    @property
    def free_from(self) -> date | None:
        return self.busy_until + timedelta(days=1) if self.busy_until else None

    def followup_question(self) -> str:
        """Если владелец не назвал сроки — обязательный уточняющий вопрос."""
        return OWNER_BUSY_FOLLOWUP

    def to_notion_update(self) -> dict:
        """Аргументы для notion_store.update_availability."""
        return {
            "status": Availability.BUSY,
            "busy_until": self.busy_until,
            "future_bookings": self.future_bookings,
        }


def airbnb_step2_message() -> str:
    """Второе сообщение в Airbnb DM после ответа владельца (контакты — на аватаре)."""
    return OWNER_AIRBNB_STEP2


# ---------- FB Marketplace -> WhatsApp ----------

def register_owner_whatsapp(listing: Listing, whatsapp: str, lead: LeadProfile) -> str:
    """Владелец дал WA-номер в переписке FB Marketplace.

    1. Номер сразу в Notion («WhatsApp контакт») — дальше канал владельца = WA.
    2. Возвращает первое WA-сообщение: запрос по датам клиента + вопрос про
       календарь объекта (Airbnb/iCal/Google-таблица) для колонки «Календарь».
    """
    from agent7 import notion_store
    from agent7.templates import OWNER_WA_ASK_CALENDAR

    whatsapp = whatsapp.strip()
    listing.owner_whatsapp = whatsapp
    if listing.page_id:
        notion_store.save_owner_whatsapp(listing.page_id, whatsapp)

    msg = owner_first_message(
        OwnerChannel.WHATSAPP,
        check_in=lead.check_in.strftime("%d.%m.%Y") if lead.check_in else "",
        check_out=lead.check_out.strftime("%d.%m.%Y") if lead.check_out else "",
        guests=lead.guests,
        listing_url=listing.source_url,
    )
    return msg + "\n\n" + OWNER_WA_ASK_CALENDAR


def register_owner_calendar(listing: Listing, url: str) -> None:
    """Владелец прислал ссылку на календарь — сохраняем в колонку «Календарь»."""
    from agent7 import notion_store

    listing.calendar_url = url.strip()
    if listing.page_id:
        notion_store.save_calendar_url(listing.page_id, listing.calendar_url)
