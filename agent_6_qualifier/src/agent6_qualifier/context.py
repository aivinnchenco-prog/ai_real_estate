"""Контекст диалога для Gemini: структурированная «база знаний» сессии.

Не vector-RAG — компактный снимок: кто клиент, какой объект, на каком шаге,
что уже спросили. Передаётся в каждый вызов Gemini вместе с историей сообщений.
"""
from __future__ import annotations

from .qualifier import Session


def dialog_stage(session: Session) -> str:
    lead = session.lead
    if session.awaiting_owner:
        return "ожидание ответа владельца"
    if session.owner_verdict:
        if session.handoff_to_human:
            return "бронь подтверждена, ждём менеджера для просмотра"
        if session.booking_intent:
            return "оформление брони: сбор ФИО и гражданства"
        return f"ответ владельца получен ({session.owner_verdict}), ждём решение по брони"
    if not (lead.check_in and lead.guests):
        return "квалификация: нужны даты и гости"
    if not session.links_sent:
        return "квалификация: даты есть, ссылки ещё не отправлены"
    if lead.budget is None and not lead.districts and not session.only_chosen:
        return "квалификация: добор района/бюджета"
    if session.awaiting_alt_consent:
        return "ждём согласия на показ альтернатив"
    if session.only_chosen:
        return "клиент выбрал только этот объект, запрос владельцу"
    if lead.preferred_object_id:
        return "объект выбран, квалификация в процессе"
    return "свободный поиск"


def build_knowledge(session: Session) -> str:
    """Текстовый блок контекста для промпта Gemini."""
    lead = session.lead
    lines = [
        f"Этап диалога: {dialog_stage(session)}",
        f"Язык клиента: {session.language}",
    ]
    if lead.name:
        lines.append(f"Имя клиента: {lead.name}")
    if lead.preferred_object_id:
        lines.append(f"Объект ID: {lead.preferred_object_id}")
    if session.source_publication_url:
        lines.append(
            f"Клиент пришёл по публикации объекта {lead.preferred_object_id or '—'}."
        )
        lines.append(f"Платформа: {session.source_platform or '—'}")
        lines.append(f"Ссылка: {session.source_publication_url}")
        lines.append(
            "Не спрашивай, какой объект его интересует, если он явно не сменил объект."
        )
    if session.chosen:
        c = session.chosen
        bits = [c.title or c.object_id]
        if c.district:
            bits.append(f"район {c.district}")
        quote = c.price_quote(lead.check_in)
        if quote:
            bits.append(quote)
        lines.append(f"Выбранный объект: {', '.join(bits)}")
    if lead.check_in:
        lines.append(
            f"Даты клиента: заезд {lead.check_in.isoformat()}"
            + (f", выезд {lead.check_out.isoformat()}" if lead.check_out else "")
        )
    if lead.guests:
        lines.append(f"Гостей: {lead.guests}")
    if lead.budget:
        lines.append(f"Бюджет: {lead.budget:,.0f} THB/мес".replace(",", " "))
    if lead.districts:
        lines.append(f"Районы: {', '.join(lead.districts)}")
    if lead.pets is True:
        lines.append("С животными: да")
    if lead.full_name:
        lines.append(f"ФИО: {lead.full_name}")
    if lead.citizenship:
        lines.append(f"Гражданство: {lead.citizenship}")
    flags = []
    if session.only_chosen:
        flags.append("только выбранный объект")
    if session.booking_intent:
        flags.append("клиент согласен на бронь")
    if session.handoff_to_human:
        flags.append("передано менеджеру")
    if session.awaiting_owner:
        flags.append("ждём владельца")
    if session.links_sent:
        flags.append("ссылки на пост/фото уже отправлены")
    if flags:
        lines.append("Флаги: " + ", ".join(flags))
    if session.history:
        last = session.history[-1]
        if last.get("role") == "assistant":
            lines.append(f"Последний вопрос агента: {last.get('text', '')[:200]}")
    return "\n".join(lines)


def format_history(history: list[dict], limit: int = 8) -> str:
    if not history:
        return "(диалог только начинается)"
    lines = []
    for msg in history[-limit:]:
        who = "Клиент" if msg.get("role") == "user" else "Агент"
        lines.append(f"{who}: {msg.get('text', '')[:300]}")
    return "\n".join(lines)
