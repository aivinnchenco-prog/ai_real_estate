"""Agent 7 Envoy: owner reply orchestration (runtime glue)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agent6_qualifier.qualifier import Session


@dataclass(frozen=True)
class OwnerMessageTemplates:
    ack_free: str
    ack_conditions: str
    busy_followup: str


def _resolve_owner_channel(session: Session, sender: Any, event: Any) -> str:
    """Channel for CRM policy — from OwnerRequest when present, else transport hint."""
    rid = getattr(session, "owner_request_id", "") or ""
    if rid:
        try:
            from agent7_envoy.owner_request_store import OwnerRequestStore

            req = OwnerRequestStore().get(rid)
            if req and req.channel:
                return req.channel
        except Exception:
            pass
    chat = str(getattr(session, "chat_id", "") or "")
    if chat.startswith("wa_"):
        return "whatsapp"
    phone = getattr(sender, "phone", None) or getattr(event, "phone", None)
    if phone and not getattr(sender, "username", None):
        return "whatsapp"
    return "telegram"


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
    send_client_message: Callable[[Any, str], Awaitable[None]],
    assign_role_folder: Callable[..., Awaitable[None]],
    notify_error: Callable[[str, str, str], None],
    templates: OwnerMessageTemplates,
    preresolved_session: Session | None = None,
    owner_channel: str | None = None,
    request_store: Any | None = None,
) -> bool:
    """Orchestrate owner reply handling. Returns True if message was consumed as owner.

    Telegram path (default): resolve via username / registry object_id.
    WhatsApp path: pass ``preresolved_session`` from WA request-specific resolver;
    TG lookup is skipped so multi-client object ambiguity cannot pick arbitrarily.

    CRM: OwnerResult is persisted locally before amo business sync.
    Source-native channels never invent raw amo chat mirrors.
    """
    username = getattr(sender, "username", "") or ""
    owner_chat_id = str(event.chat_id)

    if preresolved_session is not None:
        session = preresolved_session
        reg = get_owner(username, owner_chat_id)
    else:
        reg = get_owner(username, owner_chat_id)
        session = store.find_awaiting_owner(username)
        if session is None and reg and reg.get("object_id"):
            session = store.find_awaiting_owner_by_object(reg["object_id"])

        if session is None:
            if reg is None:
                return False
            print(
                f"[owner:@{username or owner_chat_id}] сообщение вне активного "
                f"запроса (объект {reg.get('object_id') or '?'}): "
                f"{(event.raw_text or '')[:80]}"
            )
            return True

    mark_owner(
        tg_username=username,
        tg_chat_id=owner_chat_id,
        object_id=session.chosen.object_id if session.chosen else "",
        owner_request_id=getattr(session, "owner_request_id", "") or "",
        client_session_chat_id=session.chat_id,
    )

    text = event.raw_text or ""
    print(f"[owner:@{username or owner_chat_id}] {text[:80]}")
    verdict = parse_owner_reply(text, session)

    if verdict.status == "busy" and not verdict.busy_until:
        await send_owner_response(event, templates.busy_followup)
        print(f"[owner:@{username or owner_chat_id}] занято без сроков -> уточняем")
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

    # Telegram chat ids are numeric ints; WhatsApp sessions use wa_<digits>.
    chat_target: Any = session.chat_id
    try:
        if str(chat_target).isdigit():
            chat_target = int(chat_target)
    except (TypeError, ValueError):
        pass
    await send_client_message(chat_target, reply)
    session.history.append({"role": "assistant", "text": reply})
    store.save(session)
    sessions_cache[session.chat_id] = session
    print(f"[out] {session.chat_id}: {reply[:80]}")

    channel = owner_channel or _resolve_owner_channel(session, sender, event)
    rid = getattr(session, "owner_request_id", "") or ""

    # 1) Persist OwnerResult locally BEFORE CRM (failure isolation)
    try:
        from agent7_envoy.crm_business_sync import persist_owner_result
        from agent7_envoy.owner_request_store import OwnerRequestStore

        req_store = request_store or OwnerRequestStore()
        if rid:
            persist_owner_result(
                req_store, rid, verdict=verdict, channel=channel
            )
            mid = getattr(event, "id", None)
            req_store.mark_message_processed(
                rid,
                str(mid) if mid is not None else f"owner-reply:{rid}",
                verdict=verdict.status,
            )
    except Exception as e:
        notify_error("owner_request.persist", str(e), f"orq={rid or '-'}")

    # 2) CRM business sync (never invents raw FB/Airbnb chat)
    try:
        from agent7_envoy.crm_business_sync import (
            attempt_raw_chat_mirror,
            sync_owner_reply_business,
        )
        from agent7_envoy.crm_visibility import resolve_owner_channel_crm_policy
        from agent7_envoy.owner_request_store import OwnerRequestStore

        mirror = attempt_raw_chat_mirror(channel=channel, amo=amo)
        if mirror.raw_chat_skipped_expected:
            print(
                f"[agent7.crm] channel={channel} "
                f"crm_visibility=BUSINESS_EVENTS_ONLY "
                f"raw_chat_mirror=SKIPPED_EXPECTED"
            )

        crm = sync_owner_reply_business(
            amo,
            session,
            channel=channel,
            verdict=verdict,
            owner_request_id=rid,
            raw_owner_text=text,
            client_reply_preview=reply,
        )
        if rid:
            try:
                store_ref = request_store or OwnerRequestStore()
                req = store_ref.get(rid)
                if req is not None:
                    req.crm_visibility = resolve_owner_channel_crm_policy(
                        channel
                    ).crm_visibility
                    req.crm_business_sync = (
                        "FAILED"
                        if crm.business_failed
                        else (
                            "SYNCED" if crm.business_synced else "SKIPPED_NO_AMO"
                        )
                    )
                    store_ref.upsert(req)
            except Exception:
                pass
        if crm.business_failed:
            notify_error(
                "amo.owner_flow",
                f"CRM_BUSINESS_EVENT_WRITE_FAILED: {crm.detail}",
                f"сделка #{getattr(session, 'amo_lead_id', '-')}",
            )
    except Exception as e:
        notify_error(
            "amo.owner_flow",
            str(e),
            f"сделка #{getattr(session, 'amo_lead_id', '-')}",
        )

    role_type = session.chosen.owner_agent_type if session.chosen else ""
    try:
        await assign_role_folder(client, sender, role_type)
    except Exception as e:
        notify_error("tg.folders", str(e), "role folder after owner reply")
    return True
