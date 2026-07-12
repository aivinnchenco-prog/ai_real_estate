"""Автоматический запуск Agent 8 из юзербота — без ручных скриптов.

Когда Qualifier решает «пора спросить владельца» (need_owner_check),
юзербот запускает auto_outreach фоновой задачей:

1. Свежий объект из Notion (контакты/календарь могли обновиться).
2. Реальная проверка календаря («Календарь»: Airbnb / iCal / Google-таблица).
3. Результат проверки -> Notion (availability, «Занято до», «Будущие брони»).
4. Даты закрыты -> клиенту сразу окно занятости (полное или частичное).
5. Даты открыты -> первое сообщение владельцу: Telegram — автоматически
   (+ реестр владельцев + папка «Собственники»), другие каналы — алерт менеджеру.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from agent7 import notion_store, owner_registry
from agent7.alerts import notify_error
from agent7.models import Availability, OwnerChannel
from agent7.templates import client_object_busy, client_object_partial

from .calendar_check import notion_update_from_precheck
from .outreach import build_outreach_plan


def busy_message_for_client(precheck, listing, lead) -> str:
    """Текст клиенту, когда календарь закрыл его даты.

    Если желаемая дата заезда свободна, но весь срок не помещается —
    честно называем окно (N ночей) и дату полной свободы.
    """
    busy_until = precheck.busy_until(lead.check_in)
    free_from = busy_until + timedelta(days=1) if busy_until else None
    free_nights = precheck.free_nights_from(lead.check_in)
    title = listing.title or listing.object_id
    if free_nights > 0:
        free_until = lead.check_in + timedelta(days=free_nights)
        return client_object_partial(
            title,
            lead.check_in.strftime("%d.%m.%Y"),
            free_nights,
            free_until.strftime("%d.%m.%Y"),
            busy_until.strftime("%d.%m.%Y") if busy_until else "?",
            free_from.strftime("%d.%m.%Y") if free_from else "?",
        )
    return client_object_busy(
        title,
        busy_until.strftime("%d.%m.%Y") if busy_until else "?",
        free_from.strftime("%d.%m.%Y") if free_from else "?",
    )


async def auto_outreach(client, session, store, amo) -> None:
    """Полный цикл Agent 8 для одной сессии. Ошибки не роняют юзербота."""
    lead = session.lead
    chat_id = session.chat_id
    try:
        listing = await asyncio.to_thread(
            notion_store.find_by_object_id, lead.preferred_object_id)
        if listing is None:
            notify_error("agent8.auto", f"объект {lead.preferred_object_id} "
                         "не найден в Notion", f"клиент chat_id={chat_id}")
            return
        session.chosen = listing

        # build_outreach_plan внутри ходит в календарь (Playwright/HTTP) — в поток.
        plan = await asyncio.to_thread(build_outreach_plan, listing, lead)

        if plan.precheck is not None:
            print(f"[agent8] календарь {listing.object_id}: "
                  f"available={plan.precheck.available} ({plan.precheck.note or 'ok'})")
            upd = notion_update_from_precheck(plan.precheck, lead.check_in)
            if upd and listing.page_id:
                try:
                    await asyncio.to_thread(
                        notion_store.update_availability,
                        listing.page_id, upd["status"],
                        busy_until=upd.get("busy_until"),
                        future_bookings=upd.get("future_bookings", ""),
                    )
                except Exception as e:
                    notify_error("notion.availability", str(e),
                                 f"объект {listing.object_id}")

        # --- даты закрыты: клиенту сразу занятость, владельцу не пишем ---
        if plan.skip_reason and plan.precheck and plan.precheck.available is False:
            msg = busy_message_for_client(plan.precheck, listing, lead)
            session.awaiting_owner = False
            session.owner_verdict = "busy"
            session.awaiting_alt_consent = True
            busy_until = plan.precheck.busy_until(lead.check_in)
            if busy_until:
                session.chosen.availability = Availability.BUSY
                session.chosen.busy_until = busy_until
            await client.send_message(int(chat_id), msg)
            session.history.append({"role": "assistant", "text": msg})
            store.save(session)
            print(f"[out] {chat_id}: {msg[:80]}")
            if amo is not None and session.amo_lead_id:
                await asyncio.to_thread(
                    amo.note_owner, session.amo_lead_id, listing.object_id,
                    f"Календарь: даты закрыты ({plan.precheck.note})")
            return

        # --- владельцу не написать: контактов нет ---
        if plan.channel is None:
            notify_error("agent8.auto", f"владельцу не написать: {plan.skip_reason}",
                         f"объект {listing.object_id}, клиент chat_id={chat_id}")
            return

        # --- Telegram: отправляем автоматически ---
        if plan.channel == OwnerChannel.TELEGRAM:
            username = plan.contact.lstrip("@")
            entity = await client.get_entity(username)
            await client.send_message(entity, plan.first_message)
            print(f"[agent8] владельцу @{username}: {plan.first_message[:80]}")

            owner_registry.mark_owner(
                tg_username=username,
                tg_chat_id=str(getattr(entity, "id", "") or ""),
                object_id=listing.object_id,
            )
            from agent7.tg_userbot import OWNERS_FOLDER, add_to_folder
            await add_to_folder(client, entity, OWNERS_FOLDER)

            session.awaiting_owner = True
            store.save(session)
            if amo is not None and session.amo_lead_id:
                await asyncio.to_thread(
                    amo.note_owner, session.amo_lead_id, listing.object_id,
                    f"Запрос владельцу (telegram): {plan.first_message[:150]}")
            return

        # --- WA / Airbnb DM / FB DM: авто-отправка не подключена — менеджеру ---
        notify_error(
            "agent8.manual_send",
            f"Отправьте владельцу вручную ({plan.channel.value}: {plan.contact})",
            f"объект {listing.object_id}, текст: {plan.first_message[:200]}",
        )
        if amo is not None and session.amo_lead_id:
            await asyncio.to_thread(
                amo.note_owner, session.amo_lead_id, listing.object_id,
                f"ТРЕБУЕТ РУЧНОЙ ОТПРАВКИ ({plan.channel.value} {plan.contact}): "
                f"{plan.first_message[:150]}")
    except Exception as e:
        notify_error("agent8.auto", repr(e),
                     f"авто-запрос владельцу не выполнен, chat_id={chat_id}")
