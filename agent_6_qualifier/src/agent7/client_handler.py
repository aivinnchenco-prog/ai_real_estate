"""Agent 6 Qualifier client-message orchestration.

Located in the legacy ``agent7`` package for compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agent7.qualifier import Session, Turn


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
    session = get_session(chat_id)
    text = event.raw_text or ""
    print(f"[in] {chat_id}: {text[:80]}")

    try:
        update = extract_lead_update(text, session)
    except Exception as e:
        notify_error(
            "gemini.extract",
            str(e),
            "поля из сообщения не извлечены, диалог продолжен по шаблонам",
        )
        update = {}

    turn = handle_message(session, text, update)
    reply = (
        turn.reply_draft if turn.skip_polish
        else polish_reply(turn.reply_draft, session.language, session.lead.name)
    )
    await send_client_response(event, reply)
    print(f"[out] {chat_id}: {reply[:80]}")

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

    await add_to_folder(client, sender, templates.clients_folder)
    ensure_amo_lead(amo, session, sender)
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
        except Exception as e:
            notify_error("amo.stage", str(e), "бронь подтверждена")
    if session.handoff_to_human or turn.handoff_to_human:
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
