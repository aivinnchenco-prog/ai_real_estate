"""Telegram-канал Agent 7: ЛИЧНЫЙ аккаунт через Telethon (MTProto), не бот.

- Слушает входящие личные сообщения.
- Прогоняет через Qualifier, отвечает (черновик полирует Gemini).
- Новый собеседник-клиент добавляется в папку «Клиенты» (собственники,
  которым пишет Agent 8, — в папку «Собственники»).
- Создаёт лид в amoCRM при первом сообщении и пишет примечания о событиях.

Первый вход:  python3 scripts/tg_login.py  (спросит телефон и код — один раз)
Запуск:       python3 -m agent7.tg_userbot
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from telethon import TelegramClient, events, functions, types

from . import brain, notion_store
from .alerts import notify_error
from .amo import AmoClient
from .context import build_knowledge, format_history
from .qualifier import Qualifier, Session
from .sessions import SessionStore

CLIENTS_FOLDER = "Клиенты"
OWNERS_FOLDER = "Собственники"

_sessions: dict[str, Session] = {}
_store = SessionStore(Path(__file__).resolve().parents[2] / "data" / "sessions")
# Защита от двойного запуска Agent 8 по одному чату (пока идёт проверка календаря).
_outreach_inflight: set[str] = set()


def load_env() -> None:
    root = Path(__file__).resolve().parents[2]
    env = root / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip())


def make_client() -> TelegramClient:
    return TelegramClient(
        os.environ.get("TG_SESSION", "agent7_userbot"),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )


def make_script_client() -> TelegramClient:
    """Клиент для вспомогательных скриптов (owner_reply, agent8_run).

    Userbot держит SQLite-сессию заблокированной, поэтому скрипты подключаются
    через копию файла сессии — auth key тот же, Telegram допускает
    параллельные подключения.
    """
    import shutil

    name = os.environ.get("TG_SESSION", "agent7_userbot")
    root = Path(__file__).resolve().parents[2]
    src = root / f"{name}.session"
    copy = root / f"{name}_script.session"
    if src.exists() and not copy.exists():
        shutil.copy(src, copy)
    # receive_updates=False: иначе вторая сессия «уводит» входящие
    # сообщения у работающего userbot (обновления шлются одной из сессий).
    return TelegramClient(str(copy.with_suffix("")),
                          int(os.environ["TG_API_ID"]),
                          os.environ["TG_API_HASH"],
                          receive_updates=False)


async def add_to_folder(client: TelegramClient, entity, folder_title: str) -> None:
    """Кладёт диалог в папку (dialog filter). Папка создаётся, если её нет."""
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())
        filters = [f for f in result.filters
                   if isinstance(f, types.DialogFilter)]
        target = next(
            (f for f in filters if getattr(f.title, "text", f.title) == folder_title),
            None,
        )
        peer = await client.get_input_entity(entity)
        if target is None:
            used_ids = [f.id for f in filters]
            new_id = max(used_ids, default=1) + 1
            target = types.DialogFilter(
                id=new_id,
                title=types.TextWithEntities(text=folder_title, entities=[]),
                pinned_peers=[], include_peers=[peer], exclude_peers=[],
            )
        else:
            if any(p == peer for p in target.include_peers):
                return
            target.include_peers.append(peer)
        await client(functions.messages.UpdateDialogFilterRequest(
            id=target.id, filter=target))
    except Exception as e:
        notify_error("tg.folders", str(e), f"папка «{folder_title}»")


def get_session(chat_id: str) -> Session:
    if chat_id not in _sessions:
        session = _store.load(chat_id) or Session(chat_id=chat_id)
        if not session.lead.source_channel:
            session.lead.source_channel = "telegram"
        _sessions[chat_id] = session
    return _sessions[chat_id]


async def handle_owner_message(client, event, sender, amo: AmoClient | None) -> bool:
    """Ответ собственника. True = сообщение обработано как владельца
    (и НЕ должно попадать в клиентский поток).

    Собственник опознаётся двумя способами:
    1. Реестр data/owners.json — контакт помечен, когда Agent 8 сам писал
       ему первым. Помеченный контакт никогда не считается новым клиентом.
    2. @username совпадает с «Telegram контакт» объекта, по которому клиент
       сейчас ждёт ответа (awaiting_owner).
    """
    from . import owner_registry

    username = getattr(sender, "username", "") or ""
    owner_chat_id = str(event.chat_id)
    reg = owner_registry.get_owner(username, owner_chat_id)

    session = _store.find_awaiting_owner(username)
    if session is None and reg and reg.get("object_id"):
        session = _store.find_awaiting_owner_by_object(reg["object_id"])

    if session is None:
        if reg is None:
            return False
        # Контакт помечен как владелец, но активного запроса нет:
        # логируем, НЕ создаём клиентскую сессию и НЕ отвечаем скриптом клиента.
        print(f"[owner:@{username or owner_chat_id}] сообщение вне активного "
              f"запроса (объект {reg.get('object_id') or '?'}): "
              f"{(event.raw_text or '')[:80]}")
        return True

    # Обогащаем реестр: теперь знаем и chat_id владельца.
    owner_registry.mark_owner(
        tg_username=username, tg_chat_id=owner_chat_id,
        object_id=session.chosen.object_id if session.chosen else "",
    )

    from agent8.owner_result import (
        apply_verdict_to_session,
        build_client_message,
        notion_availability_update,
        parse_owner_reply,
    )
    from .templates import OWNER_ACK_CONDITIONS, OWNER_ACK_FREE, OWNER_BUSY_FOLLOWUP

    text = event.raw_text or ""
    print(f"[owner:@{username}] {text[:80]}")
    verdict = parse_owner_reply(text, session)

    # Владелец сказал «занято», но не назвал сроки — обязательный уточняющий вопрос.
    if verdict.status == "busy" and not verdict.busy_until:
        await event.respond(OWNER_BUSY_FOLLOWUP)
        print(f"[owner:@{username}] занято без сроков -> уточняем")
        return True

    ack = OWNER_ACK_FREE if verdict.status == "free" else OWNER_ACK_CONDITIONS
    await event.respond(ack)

    client_msg = build_client_message(verdict, session)
    reply = brain.polish_reply(client_msg, session.language, session.lead.name)
    apply_verdict_to_session(session, verdict)

    if session.chosen and session.chosen.page_id:
        try:
            upd = notion_availability_update(verdict)
            notion_store.update_availability(
                session.chosen.page_id, upd["status"],
                busy_until=upd.get("busy_until"),
                future_bookings=upd.get("future_bookings", ""),
            )
        except Exception as e:
            notify_error("notion.availability", str(e), f"объект {session.chosen.object_id}")

    await client.send_message(int(session.chat_id), reply)
    session.history.append({"role": "assistant", "text": reply})
    _store.save(session)
    _sessions[session.chat_id] = session   # обновляем и горячий кэш userbot
    print(f"[out] {session.chat_id}: {reply[:80]}")

    await add_to_folder(client, sender, OWNERS_FOLDER)
    if amo is not None and session.amo_lead_id:
        try:
            stages = amo.ensure_pipeline()
            amo.update_lead_status(session.amo_lead_id, stages["Согласование условий"])
            obj = session.lead.preferred_object_id or "-"
            amo.note_owner(session.amo_lead_id, obj, text)
            amo.note_client(session.amo_lead_id, obj, f"Сообщено клиенту: {reply[:200]}")
        except Exception as e:
            notify_error("amo.owner_flow", str(e), f"сделка #{session.amo_lead_id}")
    return True


def ensure_amo_lead(amo: AmoClient | None, session: Session, sender) -> None:
    """Первое сообщение клиента -> контакт + сделка в amo (дедуп по username/телефону)."""
    if amo is None or session.amo_lead_id is not None:
        return
    try:
        username = getattr(sender, "username", "") or ""
        phone = getattr(sender, "phone", "") or ""
        display = " ".join(filter(None, [getattr(sender, "first_name", ""),
                                         getattr(sender, "last_name", "")]))
        existing = amo.find_contact(phone or username) if (phone or username) else None
        contact_id = existing["id"] if existing else amo.create_contact(
            display or username or "Клиент TG", phone=phone, tg_username=username)
        stages = amo.ensure_pipeline()
        # Дедуп: у контакта уже есть открытая сделка в нашей воронке -> продолжаем её.
        open_lead = amo.find_open_lead(contact_id) if existing else None
        if open_lead:
            session.amo_lead_id = open_lead
            print(f"[amo] продолжаем открытую сделку #{open_lead}")
            return
        fields = amo.ensure_lead_fields()
        session.amo_lead_id = amo.create_lead(
            session.lead, contact_id, stages["Новый лид"], fields)
        print(f"[amo] создана сделка #{session.amo_lead_id}")
    except Exception as e:
        notify_error("amo.create_lead", str(e),
                     f"клиент chat_id={session.chat_id}, лид НЕ создан в CRM")


async def main() -> None:
    load_env()
    client = make_client()
    qualifier = Qualifier(
        find_by_id=notion_store.find_by_object_id,
        fetch_all=notion_store.fetch_all_listings,
    )
    try:
        amo: AmoClient | None = AmoClient()
    except Exception as e:
        amo = None
        notify_error("amo.init", str(e), "агент работает БЕЗ CRM — лиды не записываются")

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event) -> None:
        if not event.is_private:
            return
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return
        chat_id = str(event.chat_id)
        try:
            # Сначала проверяем: не собственник ли это отвечает на наш запрос.
            try:
                if await handle_owner_message(client, event, sender, amo):
                    return
            except Exception as e:
                notify_error("agent8.owner_reply", repr(e),
                             f"ответ владельца @{getattr(sender, 'username', '?')} "
                             "не обработан")
                return

            session = get_session(chat_id)
            text = event.raw_text or ""
            print(f"[in] {chat_id}: {text[:80]}")

            try:
                update = brain.extract_lead_update(
                    text, session.lead,
                    context=build_knowledge(session),
                    history=format_history(session.history),
                )
            except Exception as e:
                notify_error("gemini.extract", str(e),
                             "поля из сообщения не извлечены, диалог продолжен по шаблонам")
                update = {}

            turn = qualifier.handle_message(session, text, update)
            reply = (
                turn.reply_draft if turn.skip_polish
                else brain.polish_reply(turn.reply_draft, session.language, session.lead.name)
            )
            await event.respond(reply)
            print(f"[out] {chat_id}: {reply[:80]}")

            session.history.append({"role": "user", "text": text})
            session.history.append({"role": "assistant", "text": reply})
            if len(session.history) > 12:
                session.history = session.history[-12:]

            await add_to_folder(client, sender, CLIENTS_FOLDER)
            ensure_amo_lead(amo, session, sender)
            # Карточка сделки: дозаполняем поля (даты, гости, бюджет, район),
            # как только Gemini извлёк новые факты из сообщения.
            if (amo is not None and session.amo_lead_id
                    and any(v is not None for v in update.values())):
                try:
                    amo.update_lead_fields(session.amo_lead_id, session.lead,
                                           amo.ensure_lead_fields())
                except Exception as e:
                    notify_error("amo.fields", str(e),
                                 f"поля сделки #{session.amo_lead_id} не обновлены")
            if amo is not None and session.amo_lead_id and turn.events:
                obj = session.lead.preferred_object_id or "-"
                for ev in turn.events:
                    try:
                        amo.note_client(session.amo_lead_id, obj, ev)
                    except Exception as e:
                        notify_error("amo.note", str(e), f"сделка #{session.amo_lead_id}")
            if turn.booking_confirmed and amo is not None and session.amo_lead_id:
                try:
                    stages = amo.ensure_pipeline()
                    amo.update_lead_status(session.amo_lead_id, stages["Бронь подтверждена"])
                    lead = session.lead
                    amo.note_client(
                        session.amo_lead_id,
                        lead.preferred_object_id or "-",
                        f"ФИО: {lead.full_name}, гражданство: {lead.citizenship}",
                    )
                except Exception as e:
                    notify_error("amo.stage", str(e), "бронь подтверждена")
            if turn.handoff_to_human:
                notify_error(
                    "manager.handoff",
                    "Нужен живой менеджер для назначения просмотра",
                    f"chat_id={chat_id}, ФИО={session.lead.full_name}, "
                    f"гражданство={session.lead.citizenship}, "
                    f"объект={session.lead.preferred_object_id}, "
                    f"сделка #{session.amo_lead_id}",
                )
            if (turn.need_owner_check and not session.owner_verdict
                    and chat_id not in _outreach_inflight):
                print(f"[agent8] авто-запрос владельцу: {session.lead.preferred_object_id}")
                if amo is not None and session.amo_lead_id:
                    try:
                        stages = amo.ensure_pipeline()
                        amo.update_lead_status(session.amo_lead_id, stages["Запрос владельцу"])
                    except Exception as e:
                        notify_error("amo.stage", str(e), "не удалось сменить стадию")
                # Полный цикл Agent 8 (календарь -> Notion -> клиент/владелец)
                # фоновой задачей: клиент уже получил «уточняю у владельца».
                from agent8.auto import auto_outreach

                _outreach_inflight.add(chat_id)

                async def _run_outreach(sess=session, cid=chat_id):
                    try:
                        await auto_outreach(client, sess, _store, amo)
                    finally:
                        _outreach_inflight.discard(cid)

                asyncio.create_task(_run_outreach())
            _store.save(session)
        except Exception as e:
            notify_error("agent7.handler", repr(e),
                         f"chat_id={chat_id}, клиент мог остаться без ответа")

    await client.start()
    me = await client.get_me()
    print(f"Agent 7 запущен от имени @{me.username or me.first_name}. Ctrl+C — стоп.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
