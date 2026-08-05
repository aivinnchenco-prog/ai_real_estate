"""Agent 7 Envoy: реальный прогон «проверить объект и связаться с владельцем».

Запуск:
  python3 scripts/agent8_run.py --chat 5041767749            # план + Notion, без отправки
  python3 scripts/agent8_run.py --chat 5041767749 --send     # + отправить сообщения в TG

Что делает:
1. Берёт сессию клиента (объект + даты).
2. Реальная проверка календаря по колонке «Календарь» (Airbnb / Sheets УК / iCal).
3. Результат проверки -> Notion (availability_status, «Занято до», «Будущие брони»).
4. Даты закрыты -> сразу сообщает клиенту (занят до X, свободен с Y) + альтернативы.
5. Даты открыты -> выбирает канал владельца (WA -> TG -> Airbnb DM -> FB DM)
   и готовит первое сообщение; --send отправляет владельцу в TG (канал telegram).

Имя скрипта `agent8_run.py` — legacy (историческая нумерация пакета `agent8`).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip())

from agent7 import notion_store  # noqa: E402
from agent7_envoy import owner_registry  # noqa: E402
from agent7.alerts import notify_error  # noqa: E402
from agent7.amo import AmoClient  # noqa: E402
from agent7.models import Availability, OwnerChannel  # noqa: E402
from agent7.sessions import SessionStore  # noqa: E402
from agent7.tg_userbot import make_script_client  # noqa: E402
from agent7_envoy.auto import busy_message_for_client  # noqa: E402
from agent7_envoy.calendar_check import format_busy_ranges, notion_update_from_precheck  # noqa: E402
from agent7_envoy.outreach import build_outreach_plan  # noqa: E402

_store = SessionStore(ROOT / "data" / "sessions")


async def send_tg(to, text: str) -> None:
    client = make_script_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("TG не авторизован — python3 scripts/tg_login.py")
    await client.send_message(to, text)
    await client.disconnect()


async def send_tg_owner(to: str, text: str) -> str:
    """Первое сообщение владельцу: отправить + папка «Собственники».

    Возвращает chat_id владельца для реестра.
    """
    from agent7.telegram_folders import OWNERS_FOLDER, add_to_folder

    client = make_script_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("TG не авторизован — python3 scripts/tg_login.py")
    entity = await client.get_entity(to)
    await client.send_message(entity, text)
    await add_to_folder(client, entity, OWNERS_FOLDER)
    await client.disconnect()
    return str(getattr(entity, "id", "") or "")


def main() -> int:
    p = argparse.ArgumentParser(description="Agent 7 Envoy: проверка дат + контакт с владельцем")
    p.add_argument("--chat", required=True, help="chat_id клиента")
    p.add_argument("--send", action="store_true", help="реально отправить сообщения в TG")
    args = p.parse_args()

    session = _store.load(args.chat)
    if session is None or not session.lead.preferred_object_id:
        print("Сессия не найдена или объект не выбран")
        return 1
    lead = session.lead

    # Свежий объект из Notion (контакты владельца могли обновиться).
    listing = notion_store.find_by_object_id(lead.preferred_object_id)
    if listing is None:
        print(f"Объект {lead.preferred_object_id} не найден в Notion")
        return 1
    session.chosen = listing

    print(f"Объект: {listing.object_id} | {listing.title[:60]}")
    print(f"Даты клиента: {lead.check_in} -> {lead.check_out}, гостей: {lead.guests}")
    print(f"Календарь: {listing.availability_calendar or '(нет — только вручную)'}")
    print(f"Контакты владельца: WA={listing.owner_whatsapp or '-'} "
          f"TG={listing.owner_telegram or '-'}")

    plan = build_outreach_plan(listing, lead)

    # --- результат проверки календаря -> Notion ---
    if plan.precheck is not None:
        print(f"\nПроверка календаря: available={plan.precheck.available} "
              f"({plan.precheck.note or 'ok'})")
        if plan.precheck.future_busy:
            print(f"Будущие брони: {format_busy_ranges(plan.precheck.future_busy)}")
        upd = notion_update_from_precheck(plan.precheck, lead.check_in)
        if upd and listing.page_id:
            notion_store.update_availability(
                listing.page_id, upd["status"],
                busy_until=upd.get("busy_until"),
                future_bookings=upd.get("future_bookings", ""),
            )
            print(f"Notion обновлён: {upd['status'].value}, "
                  f"занято до {upd.get('busy_until') or '-'}")
    else:
        print("\nПроверка календаря: недоступна (пишем владельцу как обычно)")

    amo_note = None
    try:
        amo = AmoClient()
        amo.ensure_pipeline()
        amo_note = amo
    except Exception as e:
        notify_error("amo.init", str(e), "agent8_run без CRM")

    # --- даты закрыты: клиенту сразу занятость + альтернативы, владельцу не пишем ---
    if plan.skip_reason and plan.precheck and plan.precheck.available is False:
        busy_until = plan.precheck.busy_until(lead.check_in)
        msg = busy_message_for_client(plan.precheck, listing, lead)
        session.awaiting_owner = False
        session.owner_verdict = "busy"
        session.awaiting_alt_consent = True
        if busy_until:
            session.chosen.availability = Availability.BUSY
            session.chosen.busy_until = busy_until
        print(f"\nДАТЫ ЗАКРЫТЫ -> сообщение клиенту:\n{msg}")
        if args.send:
            asyncio.run(send_tg(int(args.chat), msg))
            session.history.append({"role": "assistant", "text": msg})
            print("Отправлено клиенту в TG")
        _store.save(session)
        if amo_note and session.amo_lead_id:
            amo_note.note_owner(session.amo_lead_id, listing.object_id,
                                f"Календарь: даты закрыты ({plan.precheck.note})")
        return 0

    # --- владельцу не написать ---
    if plan.channel is None:
        print(f"\nВладельцу не пишем: {plan.skip_reason}")
        return 0

    # --- пишем владельцу ---
    print(f"\nКанал владельца: {plan.channel.value} -> {plan.contact}")
    print(f"Первое сообщение владельцу:\n{plan.first_message}")
    if args.send and plan.channel == OwnerChannel.TELEGRAM:
        to = plan.contact.lstrip("@")
        owner_chat_id = asyncio.run(send_tg_owner(to, plan.first_message))
        # Помечаем контакт как владельца (реестр + папка «Собственники»):
        # его ответы никогда не обрабатываются юзерботом как новый клиент.
        owner_registry.mark_owner(tg_username=to, tg_chat_id=owner_chat_id,
                                  object_id=listing.object_id)
        session.awaiting_owner = True
        _store.save(session)
        print(f"Отправлено владельцу в TG (@{to})")
        if amo_note and session.amo_lead_id:
            amo_note.note_owner(session.amo_lead_id, listing.object_id,
                                f"Запрос владельцу ({plan.channel.value}): "
                                f"{plan.first_message[:150]}")
    elif args.send:
        print(f"Авто-отправка для канала {plan.channel.value} пока не подключена "
              "(WhatsApp/Airbnb DM/FB DM) — отправьте вручную.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
