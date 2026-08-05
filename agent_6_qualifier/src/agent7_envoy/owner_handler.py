"""Agent 7 Envoy: owner reply orchestration (runtime glue)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agent7.qualifier import Session


@dataclass(frozen=True)
class OwnerMessageTemplates:
    ack_free: str
    ack_conditions: str
    busy_followup: str


async def process_owner_message(
    *,
    client: Any,
    event: Any,
    sender: Any,
    amo: Any | None,
    store: Any,
    sessions_cache: dict[str, Session],
    get_owner: Callable[..., dict | None],
    mark_owner: Callable[..., None],
    parse_owner_reply: Callable[[str, Session], Any],
    build_client_message: Callable[..., str],
    apply_verdict_to_session: Callable[..., None],
    notion_availability_update: Callable[..., dict],
    update_notion_availability: Callable[..., None],
    polish_reply: Callable[[str, str, str], str],
    send_owner_response: Callable[..., Awaitable[None]],
    send_client_message: Callable[[int, str], Awaitable[None]],
    add_to_folder: Callable[..., Awaitable[None]],
    notify_error: Callable[[str, str, str], None],
    owners_folder: str,
    templates: OwnerMessageTemplates,
) -> bool:
    """Orchestrate owner reply handling. Returns True if message was consumed as owner."""
    username = getattr(sender, "username", "") or ""
    owner_chat_id = str(event.chat_id)
    reg = get_owner(username, owner_chat_id)

    session = store.find_awaiting_owner(username)
    if session is None and reg and reg.get("object_id"):
        session = store.find_awaiting_owner_by_object(reg["object_id"])

    if session is None:
        if reg is None:
            return False
        print(f"[owner:@{username or owner_chat_id}] сообщение вне активного "
              f"запроса (объект {reg.get('object_id') or '?'}): "
              f"{(event.raw_text or '')[:80]}")
        return True

    mark_owner(
        tg_username=username, tg_chat_id=owner_chat_id,
        object_id=session.chosen.object_id if session.chosen else "",
    )

    text = event.raw_text or ""
    print(f"[owner:@{username}] {text[:80]}")
    verdict = parse_owner_reply(text, session)

    if verdict.status == "busy" and not verdict.busy_until:
        await send_owner_response(event, templates.busy_followup)
        print(f"[owner:@{username}] занято без сроков -> уточняем")
        return True

    ack = templates.ack_free if verdict.status == "free" else templates.ack_conditions
    await send_owner_response(event, ack)

    client_msg = build_client_message(verdict, session)
    reply = polish_reply(client_msg, session.language, session.lead.name)
    apply_verdict_to_session(session, verdict)

    if session.chosen and session.chosen.page_id:
        try:
            upd = notion_availability_update(verdict)
            update_notion_availability(
                session.chosen.page_id, upd["status"],
                busy_until=upd.get("busy_until"),
                future_bookings=upd.get("future_bookings", ""),
            )
        except Exception as e:
            notify_error("notion.availability", str(e), f"объект {session.chosen.object_id}")

    await send_client_message(int(session.chat_id), reply)
    session.history.append({"role": "assistant", "text": reply})
    store.save(session)
    sessions_cache[session.chat_id] = session
    print(f"[out] {session.chat_id}: {reply[:80]}")

    await add_to_folder(client, sender, owners_folder)
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
