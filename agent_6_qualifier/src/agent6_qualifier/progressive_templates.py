"""Progressive qualification replies — summary + 1–2 questions, no bulk questionnaire."""
from __future__ import annotations

import re

from .models import LeadProfile
from .morphology import bedrooms_phrase, guests_phrase, months_phrase
from .slot_planner import Slot, SlotPlan

_FRUSTRATION_RE = re.compile(
    r"(?i)(уже\s+(третий|3|второй|2)\s+раз|"
    r"ты\s+не\s+понял|не\s+понял|бесит|"
    r"вы\s+бот|бот\s+или\s+человек|"
    r"спрашиваю\s+варианты)"
)


def build_known_summary(lead: LeadProfile, plan: SlotPlan) -> str:
    """Implicit confirmation — short recap of known facts."""
    bits: list[str] = []
    if lead.districts:
        bits.append(", ".join(lead.districts))
    if lead.budget is not None:
        bits.append(f"до {lead.budget:,.0f} ฿".replace(",", " "))
    if lead.stay_months is not None:
        months = float(lead.stay_months)
        bits.append("на год" if months >= 11 else f"на {months_phrase(months)}")
    if lead.check_in is not None:
        bits.append(f"заезд {lead.check_in.strftime('%d.%m.%Y')}")
    if lead.guests is not None:
        bits.append(guests_phrase(lead.guests))
    if lead.bedrooms is not None:
        bits.append(bedrooms_phrase(lead.bedrooms))
    if not bits:
        return ""
    return "Понял: " + ", ".join(bits) + "."


def _question_for_slot(slot: Slot, lead: LeadProfile) -> str:
    if slot == Slot.CHECK_IN:
        return (
            "Подскажите, пожалуйста, дату заезда. "
            "Дату выезда можно не указывать, если контракт на год."
        )
    if slot == Slot.GUESTS:
        return "Сколько человек будет проживать?"
    if slot == Slot.DISTRICTS:
        return "Какой район вам интересует?"
    if slot == Slot.BUDGET:
        return "Какой бюджет в месяц вам комфортен?"
    if slot == Slot.BEDROOMS:
        return "Сколько спален нужно?"
    if slot == Slot.STAY_MONTHS:
        return "На какой срок планируете аренду (месяцев)?"
    if slot == Slot.PETS:
        return "Будут ли с вами питомцы?"
    return "Подскажите, пожалуйста, ещё один параметр для подбора."


def build_progressive_questions(plan: SlotPlan, lead: LeadProfile) -> list[str]:
    """All missing critical slots in one message; no one-by-one drip."""
    if not plan.next_slots:
        return []
    return [_question_for_slot(slot, lead) for slot in plan.next_slots]


def build_progressive_reply(
    lead: LeadProfile,
    plan: SlotPlan,
    *,
    message: str = "",
    prefix: str = "",
) -> str:
    """Combine optional prefix, known summary, and progressive questions."""
    parts: list[str] = []
    if prefix:
        parts.append(prefix.strip())

    frustration = bool(message and _FRUSTRATION_RE.search(message))
    summary = build_known_summary(lead, plan)
    if summary:
        parts.append(summary)

    if frustration and plan.source_flow.value == "open_search":
        if Slot.DISTRICTS not in plan.next_slots and not lead.districts:
            parts.append("Какой район вам интересует?")
            return "\n\n".join(parts)

    questions = build_progressive_questions(plan, lead)
    if frustration and not questions and plan.source_flow.value == "open_search":
        parts.append("Какой район вам интересует?")
    else:
        parts.extend(questions)

    if frustration and "бот" in (message or "").lower():
        parts.insert(
            0,
            "Я ассистент агентства — помогаю с подбором и проверкой объектов. "
            "Менеджер подключится, если нужно.",
        )

    return "\n\n".join(parts)
