"""WhatsApp Agent 7 owner inbound — same process_owner_message as Telegram."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from agent6_qualifier.messaging.conversation_key import (
    whatsapp_conversation_key,
    whatsapp_session_chat_id,
)
from agent6_qualifier.messaging.outbound_guard import (
    prepare_outbound_request,
    send_text_guarded,
)
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    assert_bot_may_send,
)
from agent6_qualifier.messaging.ownership_store import OwnershipStore
from agent6_qualifier.messaging.types import CanonicalInboundMessage, ConversationOwner
from agent6_qualifier.messaging.wazzup_config import WazzupConfig, load_wazzup_config
from agent6_qualifier.messaging.wazzup_transport import WazzupWhatsAppTransport
from agent6_qualifier.sessions import SessionStore

logger = logging.getLogger(__name__)


@dataclass
class WhatsAppOwnerTurnResult:
    processed: bool
    conversation_key: str = ""
    owner_phone: str = ""
    client_session_chat_id: str = ""
    owner_reply_preview: str = ""
    client_notified: bool = False
    resolve_code: str = ""
    notes: list[str] = field(default_factory=list)

    def to_log_lines(self) -> list[str]:
        lines = [
            "WA AGENT7 OWNER TURN",
            f"processed={self.processed}",
            f"resolve_code={self.resolve_code or '-'}",
            f"conversation_key={self.conversation_key}",
            f"client_session={self.client_session_chat_id or '-'}",
            f"client_notified={self.client_notified}",
        ]
        if self.owner_reply_preview:
            lines.append(f'reply_preview="{self.owner_reply_preview}"')
        for note in self.notes:
            lines.append(f"note={note}")
        return lines


def is_whatsapp_owner_inbound(
    message: CanonicalInboundMessage,
    store: SessionStore,
) -> bool:
    """True when inbound should be treated as owner reply, not new client lead."""
    if message.direction == "outbound":
        return False
    phone = message.phone or message.chat_id or ""
    if not phone:
        return False
    from agent7_envoy import owner_registry
    from agent7_envoy.owner_request_store import OwnerRequestStore

    if owner_registry.get_owner(whatsapp=phone):
        return True
    if OwnerRequestStore().list_open_for_owner(phone):
        return True
    if store.list_awaiting_owner_by_whatsapp(phone):
        return True
    return False


async def process_whatsapp_owner_turn(
    message: CanonicalInboundMessage,
    *,
    config: WazzupConfig | None = None,
    ownership: ConversationOwnershipState | None = None,
    store: SessionStore | None = None,
    event_store: Any = None,
    amo: Any | None = None,
    transport: WazzupWhatsAppTransport | None = None,
    request_store: Any = None,
    ownership_store: OwnershipStore | None = None,
) -> WhatsAppOwnerTurnResult:
    from agent6_qualifier import brain, notion_store
    from agent6_qualifier.alerts import notify_error
    from agent6_qualifier.messaging.wa_client_runtime import default_session_store
    from agent6_qualifier.templates import (
        OWNER_ACK_CONDITIONS,
        OWNER_ACK_FREE,
        OWNER_BUSY_FOLLOWUP,
    )
    from agent7_envoy import owner_registry
    from agent7_envoy.owner_handler import OwnerMessageTemplates, process_owner_message
    from agent7_envoy.owner_request_store import OwnerRequestStore
    from agent7_envoy.owner_result import (
        apply_verdict_to_session,
        build_client_message,
        notion_availability_update,
        parse_owner_reply,
    )
    from agent7_envoy.wa_owner_resolve import resolve_whatsapp_owner_session

    cfg = config or load_wazzup_config()
    session_store = store or default_session_store()
    req_store = request_store or OwnerRequestStore()
    own_store = ownership_store or OwnershipStore()
    wa = transport or WazzupWhatsAppTransport(cfg)
    phone = message.phone or ""
    conversation_key = whatsapp_conversation_key(phone, chat_id=message.chat_id)
    result = WhatsAppOwnerTurnResult(
        processed=False,
        conversation_key=conversation_key,
        owner_phone=phone,
        owner_reply_preview=(message.text or "")[:160],
    )

    reg = owner_registry.get_owner(whatsapp=phone)
    resolved = resolve_whatsapp_owner_session(
        owner_phone=phone,
        session_store=session_store,
        request_store=req_store,
        registry_entry=reg,
        message_id=message.message_id or "",
    )
    result.resolve_code = resolved.code
    if resolved.code == "OWNER_REPLY_DUPLICATE":
        result.processed = True
        result.notes.append("OWNER_REPLY_DUPLICATE — continuation suppressed")
        if resolved.request:
            result.client_session_chat_id = resolved.request.client_session_chat_id
        return result
    if resolved.code != "OK" or resolved.session is None:
        result.notes.append(resolved.code)
        if resolved.reason:
            result.notes.append(resolved.reason)
        return result

    session = resolved.session
    result.client_session_chat_id = session.chat_id
    owner_req = resolved.request
    client_notified = {"ok": False}
    verdict_box: dict[str, str] = {"status": ""}

    async def send_owner_response(_event: Any, text: str) -> None:
        owner_state = ownership or ConversationOwnershipState(
            chat_id=str(message.chat_id or phone),
            owner=ConversationOwner.BOT_ACTIVE,
        )
        req = prepare_outbound_request(
            recipient_chat_id=str(phone or message.chat_id or ""),
            text=text or "",
            channel_id=cfg.channel_id,
        )
        dry = not (cfg.send_enabled and cfg.auto_reply_enabled)
        if not cfg.phone_allowed_for_live(phone):
            result.notes.append("owner ack blocked: allowlist")
            return
        try:
            assert_bot_may_send(owner_state)
        except PermissionError as exc:
            result.notes.append(f"owner ack blocked: {exc}")
            return
        send_text_guarded(
            wa,
            req,
            ownership=owner_state,
            store=event_store,
            dry_run=dry,
        )

    async def send_client_message(chat_id: Any, reply: str) -> None:
        session_local = session_store.load(str(chat_id)) or session
        client_phone = ""
        if session_local is not None:
            client_phone = session_local.lead.whatsapp or ""
            result.client_session_chat_id = session_local.chat_id
        if not client_phone and str(chat_id).startswith("wa_"):
            client_phone = "+" + str(chat_id)[3:]
        if not client_phone:
            result.notes.append("client phone missing for owner→client notify")
            return
        if not cfg.phone_allowed_for_live(client_phone):
            result.notes.append("client notify blocked: allowlist")
            return

        client_ownership = own_store.get(
            chat_id=str(chat_id), phone=client_phone
        )
        if client_ownership is None:
            result.notes.append(
                "client notify blocked: ownership unknown (no fake BOT_ACTIVE)"
            )
            return
        try:
            assert_bot_may_send(client_ownership)
        except PermissionError as exc:
            result.notes.append(f"client notify blocked: {exc}")
            return

        req = prepare_outbound_request(
            recipient_chat_id=client_phone,
            text=reply or "",
            channel_id=cfg.channel_id,
        )
        dry = not (cfg.send_enabled and cfg.auto_reply_enabled)
        send_text_guarded(
            wa,
            req,
            ownership=client_ownership,
            store=event_store,
            dry_run=dry,
        )
        client_notified["ok"] = True

    async def _noop_folder(*_a, **_k) -> None:
        return None

    def _parse_and_track(text: str, sess: Any) -> Any:
        verdict = parse_owner_reply(text, sess)
        verdict_box["status"] = getattr(verdict, "status", "") or ""
        return verdict

    event = SimpleNamespace(
        raw_text=message.text or "",
        chat_id=whatsapp_session_chat_id(phone, chat_id=message.chat_id),
        message_id=message.message_id,
    )
    sender = SimpleNamespace(
        username="",
        phone=phone,
        first_name=message.sender_name or "",
    )

    consumed = await process_owner_message(
        client=SimpleNamespace(provider="wazzup"),
        event=event,
        sender=sender,
        amo=amo,
        store=session_store,
        sessions_cache={},
        get_owner=lambda tg_username="", tg_chat_id="", whatsapp="": owner_registry.get_owner(
            tg_username=tg_username,
            tg_chat_id=tg_chat_id,
            whatsapp=whatsapp or phone,
        ),
        mark_owner=lambda **kwargs: owner_registry.mark_owner(
            whatsapp=kwargs.get("whatsapp") or phone,
            object_id=kwargs.get("object_id") or "",
            channel="whatsapp",
            tg_username=kwargs.get("tg_username") or "",
            tg_chat_id=kwargs.get("tg_chat_id") or "",
            owner_request_id=kwargs.get("owner_request_id")
            or (owner_req.owner_request_id if owner_req else "")
            or getattr(session, "owner_request_id", ""),
            client_session_chat_id=kwargs.get("client_session_chat_id")
            or session.chat_id,
        ),
        parse_owner_reply=_parse_and_track,
        build_client_message=build_client_message,
        apply_verdict_to_session=apply_verdict_to_session,
        notion_availability_update=notion_availability_update,
        update_notion_availability=notion_store.update_availability,
        polish_reply=brain.polish_reply,
        send_owner_response=send_owner_response,
        send_client_message=send_client_message,
        assign_role_folder=_noop_folder,
        notify_error=notify_error,
        templates=OwnerMessageTemplates(
            ack_free=OWNER_ACK_FREE,
            ack_conditions=OWNER_ACK_CONDITIONS,
            busy_followup=OWNER_BUSY_FOLLOWUP,
        ),
        preresolved_session=session,
    )
    result.processed = bool(consumed)
    result.client_notified = bool(client_notified["ok"])
    if owner_req is not None and consumed:
        # busy-without-until keeps request open for follow-up; otherwise resolve.
        resolve = verdict_box["status"] in {
            "free",
            "conditions_changed",
            "busy",
        } and not (
            verdict_box["status"] == "busy"
            and not getattr(session, "owner_verdict", "")
        )
        # If busy follow-up path left awaiting_owner True, do not resolve yet.
        if session.awaiting_owner and not session.owner_verdict:
            req_store.mark_message_processed(
                owner_req.owner_request_id,
                message.message_id or "",
                verdict=verdict_box["status"],
                resolve=False,
            )
        else:
            req_store.mark_message_processed(
                owner_req.owner_request_id,
                message.message_id or "",
                verdict=verdict_box["status"] or session.owner_verdict,
                resolve=True,
            )
    if not consumed:
        result.notes.append("owner message not linked to awaiting session")
    return result
