"""Симуляция ответа владельца и уведомление клиента (тест / ручной шаг Agent 8).

Запуск:
  python3 scripts/owner_reply.py --chat 5041767749 --reply "Да, свободно на эти даты"

Что делает:
1. Загружает сессию клиента (должна быть awaiting_owner).
2. Gemini разбирает ответ владельца.
3. Пишет availability в Notion.
4. Отправляет сообщение клиенту в Telegram.
5. Обновляет стадию сделки в amoCRM → «Согласование условий».
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

from agent7 import brain, notion_store  # noqa: E402
from agent7.alerts import notify_error  # noqa: E402
from agent7.amo import AmoClient  # noqa: E402
from agent7.sessions import SessionStore  # noqa: E402
from agent7.tg_userbot import make_script_client  # noqa: E402
from agent8.owner_result import (  # noqa: E402
    apply_verdict_to_session,
    build_client_message,
    notion_availability_update,
    parse_owner_reply,
)

_store = SessionStore(ROOT / "data" / "sessions")


async def send_tg(chat_id: str, text: str) -> None:
    client = make_script_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("TG не авторизован — запустите python3 scripts/tg_login.py")
    await client.send_message(int(chat_id), text)
    await client.disconnect()


def main() -> int:
    p = argparse.ArgumentParser(description="Ответ владельца → сообщение клиенту")
    p.add_argument("--chat", required=True, help="chat_id клиента в Telegram")
    p.add_argument("--reply", required=True, help="текст ответа владельца")
    args = p.parse_args()

    session = _store.load(args.chat)
    if session is None:
        print(f"Сессия {args.chat} не найдена")
        return 1
    if not session.awaiting_owner and not session.lead.preferred_object_id:
        print("Сессия не в статусе ожидания владельца — продолжаем всё равно")

    try:
        verdict = parse_owner_reply(args.reply, session)
    except Exception as e:
        notify_error("gemini.owner_parse", str(e), f"chat={args.chat}")
        print(f"Ошибка разбора ответа владельца: {e}")
        return 1

    draft = build_client_message(verdict, session)
    reply = brain.polish_reply(draft, session.language, session.lead.name)
    apply_verdict_to_session(session, verdict)

    if session.chosen and session.chosen.page_id:
        upd = notion_availability_update(verdict)
        notion_store.update_availability(
            session.chosen.page_id,
            upd["status"],
            busy_until=upd.get("busy_until"),
            future_bookings=upd.get("future_bookings", ""),
        )
        print(f"Notion обновлён: {upd['status'].value}")

    try:
        asyncio.run(send_tg(args.chat, reply))
        print(f"Клиенту отправлено: {reply[:120]}...")
    except Exception as e:
        notify_error("tg.send_client", str(e), f"chat={args.chat}")
        print(f"Не удалось отправить в TG: {e}")
        return 1

    session.history.append({"role": "assistant", "text": reply})
    _store.save(session)

    try:
        amo = AmoClient()
        stages = amo.ensure_pipeline()
        if session.amo_lead_id:
            amo.update_lead_status(session.amo_lead_id, stages["Согласование условий"])
            obj = session.lead.preferred_object_id or "-"
            amo.note_owner(session.amo_lead_id, obj, args.reply)
            amo.note_client(session.amo_lead_id, obj, f"Сообщено клиенту: {reply[:200]}")
            print(f"amoCRM сделка #{session.amo_lead_id} → Согласование условий")
    except Exception as e:
        notify_error("amo.owner_flow", str(e), f"сделка #{session.amo_lead_id}")

    print("Готово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
