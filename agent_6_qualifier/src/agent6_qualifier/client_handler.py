"""Agent 6 Qualifier client-message orchestration.

Located in canonical package ``agent6_qualifier``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .qualifier import Session, Turn


def _amo_tasks(amo, notify_error_fn):
    if amo is None:
        return None
    from .amo_task_service import AmoTaskService
    return AmoTaskService(amo, notify_error_fn=notify_error_fn)


@dataclass(frozen=True)
class ClientMessageTemplates:
    notary_caption: str
    clients_folder: str
    amo_stage_owner_request: str
    amo_stage_booking_confirmed: str
    history_max: int = 12


async def process_client_message(
    *,
    event: Any,
    sender: Any,
    client: Any,
    amo: Any | None,
    chat_id: str,
    store: Any,
    outreach_inflight: set[str],
    get_session: Callable[[str], Session],
    extract_lead_update: Callable[[str, Session], dict],
    handle_message: Callable[[Session, str, dict], Turn],
    polish_reply: Callable[[str, str, str], str],
    send_client_response: Callable[..., Awaitable[None]],
    process_confirmed_booking: Callable[..., Awaitable[Any]],
    generate_booking_doc: Callable[..., Any],
    add_to_folder: Callable[..., Awaitable[None]],
    ensure_amo_lead: Callable[..., None],
    notify_manager: Callable[..., Any],
    notify_error: Callable[[str, str, str], None],
    auto_outreach: Callable[..., Awaitable[None]],
    create_task: Callable[..., Any],
    templates: ClientMessageTemplates,
) -> None:
    """Orchestrate client message handling after owner gate."""
    # CLIENT_CONVERSATION_STARTED — confirmed client channel (TG or WA).
    # Fail-safe: never blocks reply. Dual sync is async when enabled.
    try:
        from agent6_qualifier.messaging.contact_role_hook import (
            on_client_conversation_started,
        )

        uname = getattr(sender, "username", "") or ""
        phone = getattr(sender, "phone", "") or ""
        channel = getattr(sender, "channel", "") or (
            "whatsapp" if str(chat_id).startswith("wa_") else "telegram"
        )
        on_client_conversation_started(
            phone=phone or None,
            tg_username=uname or None,
            tg_chat_id=str(chat_id) if channel == "telegram" else None,
            source="AGENT6_INBOUND",
            metadata={"channel": channel, "chat_id": str(chat_id)},
        )
    except Exception:
        pass

    session = get_session(chat_id)
    text = event.raw_text or ""
    print(f"[in] {chat_id}: {text[:80]}")
    if amo is not None and session.amo_lead_id:
        try:
            from .handoff_control import sync_from_amo_lead

            sync_from_amo_lead(amo, session)
        except Exception:
            pass

    tasks = _amo_tasks(amo, notify_error)
    if tasks is not None:
        tasks.on_client_inbound(session)

    try:
        update = extract_lead_update(text, session)
    except Exception as e:
        notify_error(
            "gemini.extract",
            str(e),
            "поля из сообщения не извлечены, диалог продолжен по шаблонам",
        )
        update = {}

    # Observability + optional polish hints — knowledge must not alter qualifier.
    playbook_hints = ""
    try:
        from agent6_qualifier.context import build_playbook_hints

        playbook_hints = build_playbook_hints(session, message=text)
        if playbook_hints.startswith("knowledge_refs:"):
            # first line already includes refs; also print compact for ops
            first = playbook_hints.splitlines()[0]
            print(f"knowledge_selected: {first.replace('knowledge_refs:', '').strip()}")
        elif "knowledge_refs:" in playbook_hints:
            for line in playbook_hints.splitlines():
                if line.startswith("knowledge_refs:"):
                    print(f"knowledge_selected: {line.split(':', 1)[1].strip()}")
                    break
    except Exception:
        print("KNOWLEDGE_FALLBACK client_handler")
        playbook_hints = ""

    turn = handle_message(session, text, update)
    if turn.silent or not (turn.reply_draft or "").strip():
        session.history.append({"role": "user", "text": text})
        store.save(session)
        print(f"[out] {chat_id}: (silent — human-owned)")
        return

    if turn.template_key:
        session.last_outbound_template_key = turn.template_key

    if turn.skip_polish:
        reply = turn.reply_draft
    else:
        try:
            reply = polish_reply(
                turn.reply_draft,
                session.language,
                session.lead.name,
                playbook_hints=playbook_hints,
            )
        except TypeError:
            # Test doubles / older callables may not accept playbook_hints.
            reply = polish_reply(
                turn.reply_draft, session.language, session.lead.name
            )

    from .outbound_dedup import should_suppress_outbound

    if should_suppress_outbound(session, reply):
        print(
            f"[out] {chat_id}: suppressed duplicate",
            flush=True,
        )
        session.history.append({"role": "user", "text": text})
        store.save(session)
        return

    sent = False

    async def _send_once(evt, text_out: str) -> None:
        nonlocal sent
        if sent:
            print(f"[out] {chat_id}: suppressed second send", flush=True)
            return
        await send_client_response(evt, text_out)
        sent = True

    await _send_once(event, reply)
    print(f"[out] {chat_id}: {reply[:80]}")

    if tasks is not None and turn.awaiting_client_response:
        tasks.on_client_outbound_awaiting_response(session, reply)

    if turn.booking_confirmed:
        uname = getattr(sender, "username", "") or ""
        contact = f"Telegram: @{uname}" if uname else f"Telegram id {chat_id}"
        notary = await process_confirmed_booking(
            session,
            contact,
            generate_doc=generate_booking_doc,
            send_doc=lambda path: client.send_file(
                event.chat_id,
                str(path),
                caption=templates.notary_caption,
                reply_to=event.message.id,
            ),
            amo_lead_id=session.amo_lead_id,
            attach_file=amo.attach_file if amo is not None else None,
            on_generation_error=lambda e: notify_error(
                "booking_doc",
                str(e),
                "договор не сформирован — бронь зафиксирована, "
                "документ нужно отправить вручную",
            ),
            on_attach_error=lambda e: notify_error(
                "amo.attach_file",
                str(e),
                f"договор не прикреплён к сделке "
                f"#{session.amo_lead_id} — приложите вручную",
            ),
        )
        if notary.sent_to_client and notary.doc_path is not None:
            print(f"[notary] договор отправлен: {notary.doc_path.name}")
        if notary.attached_to_amo:
            print(f"[notary] договор прикреплён к сделке #{session.amo_lead_id}")

    session.history.append({"role": "user", "text": text})
    session.history.append({"role": "assistant", "text": reply})
    if len(session.history) > templates.history_max:
        session.history = session.history[-templates.history_max:]

    try:
        await add_to_folder(client, sender, templates.clients_folder)
    except Exception as e:
        notify_error("tg.folders", str(e), f"папка «{templates.clients_folder}»")
    ensure_amo_lead(amo, session, sender)
    if (
        amo is not None
        and session.amo_lead_id
        and session.source_publication_url
    ):
        try:
            amo.note_client(
                session.amo_lead_id,
                session.lead.preferred_object_id or "-",
                f"Источник публикации: {session.source_platform or '-'} "
                f"{session.source_publication_url}",
            )
        except Exception as e:
            notify_error(
                "amo.note",
                str(e),
                f"источник публикации не записан в сделку #{session.amo_lead_id}",
            )
    if (amo is not None and session.amo_lead_id
            and any(v is not None for v in update.values())):
        try:
            amo.update_lead_fields(
                session.amo_lead_id, session.lead, amo.ensure_lead_fields(),
            )
        except Exception as e:
            notify_error(
                "amo.fields",
                str(e),
                f"поля сделки #{session.amo_lead_id} не обновлены",
            )
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
            amo.update_lead_status(
                session.amo_lead_id,
                stages[templates.amo_stage_booking_confirmed],
            )
            lead = session.lead
            amo.update_lead_fields(
                session.amo_lead_id, lead, amo.ensure_lead_fields(),
            )
            amo.note_client(
                session.amo_lead_id,
                lead.preferred_object_id or "-",
                f"ФИО: {lead.full_name}, гражданство: {lead.citizenship}, "
                f"WhatsApp: {lead.whatsapp}, гостей: {lead.guests}",
            )
            if tasks is not None:
                tasks.reconcile_stage(
                    session.amo_lead_id, templates.amo_stage_booking_confirmed,
                )
        except Exception as e:
            notify_error("amo.stage", str(e), "бронь подтверждена")
    if session.handoff_to_human or turn.handoff_to_human:
        # Structured summary so the manager does not have to read the chat.
        # Added alongside existing notes, never replacing them.
        try:
            from agent6_qualifier.handoff_note import post_handoff_note

            post_handoff_note(
                amo, session, last_client_message=text, notify_error=notify_error,
            )
        except Exception as e:
            notify_error("amo.handoff_note", str(e), "structured handoff note")
        if tasks is not None:
            tasks.on_need_human(session, text, notify_fn=notify_manager, sender=sender)
        else:
            uname = getattr(sender, "username", "") or ""
            who = f"@{uname}" if uname else f"chat_id={chat_id}"
            notify_manager(
                f"Клиент {who} ожидает ответа менеджера.\n"
                f"Объект: {session.lead.preferred_object_id or '-'}, "
                f"сделка #{session.amo_lead_id or '-'}\n"
                f"ФИО: {session.lead.full_name or '-'}, "
                f"гражданство: {session.lead.citizenship or '-'}\n"
                f"Сообщение: {text[:200]}",
                dedup_key=chat_id,
            )
    if (turn.need_owner_check and not session.owner_verdict
            and chat_id not in outreach_inflight):
        print(f"[agent8] авто-запрос владельцу: {session.lead.preferred_object_id}")
        if amo is not None and session.amo_lead_id:
            try:
                stages = amo.ensure_pipeline()
                amo.update_lead_status(
                    session.amo_lead_id,
                    stages[templates.amo_stage_owner_request],
                )
                if tasks is not None:
                    tasks.reconcile_stage(
                        session.amo_lead_id, templates.amo_stage_owner_request,
                    )
            except Exception as e:
                notify_error("amo.stage", str(e), "не удалось сменить стадию")
        outreach_inflight.add(chat_id)

        async def _run_outreach(sess=session, cid=chat_id):
            try:
                await auto_outreach(client, sess, store, amo)
            finally:
                outreach_inflight.discard(cid)

        create_task(_run_outreach())
    store.save(session)


@dataclass
class ClientHandleResult:
    turn: Turn
    reply: str
    silent: bool


def process_client_message_sync(
    session: Session,
    message: str,
    qualifier: Any,
    *,
    polish: bool = True,
) -> ClientHandleResult:
    """Sync qualifier pipeline for tests and WA/TG parity checks."""
    from . import brain
    from .context import build_knowledge, format_history

    try:
        update = brain.extract_lead_update(
            message,
            session.lead,
            context=build_knowledge(session),
            history=format_history(session.history),
        )
    except Exception:
        update = {}

    turn = qualifier.handle_message(session, message, update)
    if turn.silent or not (turn.reply_draft or "").strip():
        return ClientHandleResult(turn=turn, reply="", silent=True)

    if turn.template_key:
        session.last_outbound_template_key = turn.template_key

    reply = turn.reply_draft
    if polish and not turn.skip_polish:
        reply = brain.polish_reply(reply, session.language, session.lead.name)

    from .outbound_dedup import should_suppress_outbound

    if should_suppress_outbound(session, reply):
        print("suppressed duplicate", flush=True)
        return ClientHandleResult(turn=turn, reply="", silent=True)

    return ClientHandleResult(turn=turn, reply=reply, silent=False)
