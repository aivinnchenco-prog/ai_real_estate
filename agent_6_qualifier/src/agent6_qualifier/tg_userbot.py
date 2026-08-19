"""Telegram-канал Agent 6 Qualifier: ЛИЧНЫЙ аккаунт через Telethon (MTProto), не бот.

Canonical package: ``agent6_qualifier``.

- Слушает входящие личные сообщения.
- Прогоняет через Qualifier, отвечает (черновик полирует Gemini).
- Новый собеседник-клиент добавляется в папку «Клиент» (владельцы/агенты —
  в «Owner» или «Agent» по роли из Notion).
- Создаёт лид в amoCRM при первом сообщении и пишет примечания о событиях.

Первый вход:  python3 scripts/tg_login.py  (спросит телефон и код — один раз)
Запуск:       python3 -m agent6_qualifier.tg_userbot
Legacy:       python3 -m agent7.tg_userbot
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from telethon import TelegramClient, events

from . import brain, notion_store
from .alerts import notify_error
from .amo import AmoClient
from .human import humanized_respond
from .qualifier import Qualifier, Session
from .sessions import SessionStore
from .runtime_paths import qualifier_session_store_dir
from .tg_account import session_file_path, session_name
from .telegram_folders import (
    CLIENT_FOLDER,
    add_to_folder,
    assign_role_folder,
)

_sessions: dict[str, Session] = {}
_outreach_inflight: set[str] = set()


def load_env() -> None:
    root = Path(__file__).resolve().parents[2]
    candidates = [
        Path(os.getenv("OPENHOME_ENV_FILE") or "/opt/openhome/.env"),
        root / ".env",
    ]
    for env in candidates:
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip())


load_env()
_store = SessionStore(qualifier_session_store_dir())


def run_schema_check(skip: bool) -> None:
    """Валидация живой схемы Notion против schema/notion_schema.json (общий контракт репо)."""
    if skip or os.environ.get("SKIP_SCHEMA_CHECK") == "1":
        print("[schema] проверка схемы пропущена (--skip-schema-check)")
        return
    for parent in Path(__file__).resolve().parents:
        validator = parent / "schema" / "validate_schema.py"
        if validator.exists():
            proc = subprocess.run([sys.executable, str(validator)])
            if proc.returncode != 0:
                print(
                    "[schema] Схема Notion не совпадает с контрактом. "
                    "Исправь таблицу/контракт или запусти с --skip-schema-check.",
                    file=sys.stderr,
                )
                sys.exit(2)
            return
    print("[schema] validate_schema.py не найден — проверка схемы пропущена", file=sys.stderr)


def make_client() -> TelegramClient:
    session = str(session_file_path().with_suffix(""))
    return TelegramClient(
        session,
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

    name = session_name()
    src = session_file_path()
    copy = src.with_name(f"{name}_script.session")
    if src.exists() and not copy.exists():
        shutil.copy(src, copy)
    # receive_updates=False: иначе вторая сессия «уводит» входящие
    # сообщения у работающего userbot (обновления шлются одной из сессий).
    return TelegramClient(str(copy.with_suffix("")),
                          int(os.environ["TG_API_ID"]),
                          os.environ["TG_API_HASH"],
                          receive_updates=False)


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
    from agent7_envoy import owner_registry
    from agent7_envoy.owner_handler import OwnerMessageTemplates, process_owner_message
    from agent7_envoy.owner_result import (
        apply_verdict_to_session,
        build_client_message,
        notion_availability_update,
        parse_owner_reply,
    )
    from .templates import OWNER_ACK_CONDITIONS, OWNER_ACK_FREE, OWNER_BUSY_FOLLOWUP

    async def _send_client_message(chat_id: int, reply: str) -> None:
        await client.send_message(chat_id, reply)

    return await process_owner_message(
        client=client,
        event=event,
        sender=sender,
        amo=amo,
        store=_store,
        sessions_cache=_sessions,
        get_owner=owner_registry.get_owner,
        mark_owner=owner_registry.mark_owner,
        parse_owner_reply=parse_owner_reply,
        build_client_message=build_client_message,
        apply_verdict_to_session=apply_verdict_to_session,
        notion_availability_update=notion_availability_update,
        update_notion_availability=notion_store.update_availability,
        polish_reply=brain.polish_reply,
        send_owner_response=humanized_respond,
        send_client_message=_send_client_message,
        assign_role_folder=assign_role_folder,
        notify_error=notify_error,
        templates=OwnerMessageTemplates(
            ack_free=OWNER_ACK_FREE,
            ack_conditions=OWNER_ACK_CONDITIONS,
            busy_followup=OWNER_BUSY_FOLLOWUP,
        ),
    )


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
    run_schema_check("--skip-schema-check" in sys.argv)
    client = make_client()
    from .publication_mapping_store import NotionPublicationMappingStore

    publication_store = NotionPublicationMappingStore(notion_store.fetch_all_pages)
    qualifier = Qualifier(
        find_by_id=notion_store.find_by_object_id,
        fetch_all=notion_store.fetch_all_listings,
        find_by_tg_post=notion_store.find_by_tg_post,
        publication_store=publication_store,
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

            from .client_handler import ClientMessageTemplates, process_client_message
            from .telegram_publication import handle_publication_reference
            from agent8_notary.booking_doc import generate_booking_doc
            from agent8_notary.service import process_confirmed_booking
            from agent7_envoy.auto import auto_outreach

            notary_caption = (
                "Соглашение о бронировании (заявка). Оплаты по нему "
                "нет — итоговые условия зафиксируем в основном "
                "договоре после просмотра."
            )

            def _extract_lead_update(message: str, session: Session) -> dict:
                from agent6_qualifier.session_extract import (
                    extract_lead_update_for_session,
                )

                return extract_lead_update_for_session(
                    message,
                    session,
                    notify_error=notify_error,
                    use_llm=True,
                )

            from .alerts import notify_manager

            session = get_session(chat_id)
            if await handle_publication_reference(
                event,
                session,
                store=publication_store,
                find_by_id=notion_store.find_by_object_id,
                respond=humanized_respond,
                notify_diagnostic=lambda msg: notify_error(
                    "publication.resolve", msg, f"chat_id={chat_id}"
                ),
            ):
                _store.save(session)
                return

            await process_client_message(
                event=event,
                sender=sender,
                client=client,
                amo=amo,
                chat_id=chat_id,
                store=_store,
                outreach_inflight=_outreach_inflight,
                get_session=get_session,
                extract_lead_update=_extract_lead_update,
                handle_message=qualifier.handle_message,
                polish_reply=brain.polish_reply,
                send_client_response=humanized_respond,
                process_confirmed_booking=process_confirmed_booking,
                generate_booking_doc=generate_booking_doc,
                add_to_folder=add_to_folder,
                ensure_amo_lead=ensure_amo_lead,
                notify_manager=notify_manager,
                notify_error=notify_error,
                auto_outreach=auto_outreach,
                create_task=asyncio.create_task,
                templates=ClientMessageTemplates(
                    notary_caption=notary_caption,
                    clients_folder=CLIENT_FOLDER,
                    amo_stage_owner_request="Запрос владельцу",
                    amo_stage_booking_confirmed="Бронь подтверждена",
                ),
            )
        except Exception as e:
            notify_error("agent7.handler", repr(e),
                         f"chat_id={chat_id}, клиент мог остаться без ответа")

    await client.start()
    me = await client.get_me()
    print(f"Agent 7 запущен от имени @{me.username or me.first_name}. Ctrl+C — стоп.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
