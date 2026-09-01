"""WhatsApp Agent 6 runtime — same process_client_message core as Telegram.

Transport-only concerns live here. Business decisions stay in Qualifier /
client_handler / Agent7 / Agent8.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent6_qualifier.messaging.conversation_key import (
    whatsapp_conversation_key,
    whatsapp_session_chat_id,
)
from agent6_qualifier.messaging.crm_resolve import resolve_existing_crm_conversation
from agent6_qualifier.messaging.document_send import send_booking_document
from agent6_qualifier.messaging.outbound_guard import (
    DuplicateOutboundSuppressed,
    prepare_outbound_request,
    send_text_guarded,
)
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    assert_bot_may_send,
    resume_bot,
)
from agent6_qualifier.messaging.types import CanonicalInboundMessage, ConversationOwner
from agent6_qualifier.messaging.wazzup_config import WazzupConfig, load_wazzup_config
from agent6_qualifier.messaging.wazzup_transport import WazzupWhatsAppTransport
from agent6_qualifier.qualifier import Qualifier, Session
from agent6_qualifier.runtime_paths import qualifier_session_store_dir
from agent6_qualifier.sessions import SessionStore

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SESSION_ROOT = _ROOT / "data" / "sessions"
_outreach_inflight: set[str] = set()
_sessions_cache: dict[str, Session] = {}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def agent7_live_outreach_enabled() -> bool:
    return _env_bool("AGENT7_LIVE_OUTREACH_ENABLED", False)


def default_session_store() -> SessionStore:
    return SessionStore(qualifier_session_store_dir())


@dataclass
class WhatsAppTurnResult:
    conversation_key: str
    session_chat_id: str
    processed: bool
    replied: bool
    reply_preview: str = ""
    outbound_mode: str = "suppressed"
    outbound_blocked_reason: str = ""
    need_owner_check: bool = False
    owner_check_status: str = ""
    booking_confirmed: bool = False
    handoff: bool = False
    amo_lead_id: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_log_lines(self) -> list[str]:
        lines = [
            "WA AGENT6 TURN",
            f"conversation_key={self.conversation_key}",
            f"session_chat_id={self.session_chat_id}",
            f"processed={self.processed}",
            f"replied={self.replied}",
            f"outbound_mode={self.outbound_mode}",
            f"need_owner_check={self.need_owner_check}",
            f"owner_check_status={self.owner_check_status or '-'}",
            f"booking_confirmed={self.booking_confirmed}",
            f"handoff={self.handoff}",
            f"amo_lead_id={self.amo_lead_id}",
        ]
        if self.reply_preview:
            lines.append(f'reply_preview="{self.reply_preview}"')
        if self.outbound_blocked_reason:
            lines.append(f"outbound_blocked_reason={self.outbound_blocked_reason}")
        for note in self.notes:
            lines.append(f"note={note}")
        return lines


class _WaEvent:
    def __init__(self, text: str, message_id: str):
        self.raw_text = text or ""
        self.message_id = message_id


def _get_or_load_session(
    store: SessionStore,
    chat_id: str,
    *,
    phone: str | None = None,
) -> Session:
    if chat_id not in _sessions_cache:
        session = store.load(chat_id) or Session(chat_id=chat_id)
        if not session.lead.source_channel:
            session.lead.source_channel = "whatsapp"
        if phone and not session.lead.whatsapp:
            session.lead.whatsapp = phone if str(phone).startswith("+") else f"+{phone}"
        _sessions_cache[chat_id] = session
    return _sessions_cache[chat_id]


def ensure_amo_lead_whatsapp(
    amo: Any | None,
    session: Session,
    *,
    phone: str | None,
    display_name: str | None = None,
) -> None:
    """Reuse existing Wazzup/amo contact+lead by phone; create only if missing."""
    if amo is None or session.amo_lead_id is not None:
        return
    try:
        crm = resolve_existing_crm_conversation(
            amo, phone=phone, chat_id=session.chat_id
        )
        if crm.lead_id:
            session.amo_lead_id = crm.lead_id
            print(f"[amo] WA reuse lead #{crm.lead_id}")
            return
        contact_id = crm.contact_id
        if contact_id is None:
            contact_id = amo.create_contact(
                display_name or phone or "Клиент WA",
                phone=phone or "",
            )
        stages = amo.ensure_pipeline()
        open_lead = amo.find_open_lead(contact_id)
        if open_lead:
            session.amo_lead_id = open_lead
            print(f"[amo] WA continue open lead #{open_lead}")
            return
        fields = amo.ensure_lead_fields()
        session.amo_lead_id = amo.create_lead(
            session.lead, contact_id, stages["Новый лид"], fields
        )
        print(f"[amo] WA created lead #{session.amo_lead_id}")
    except Exception as exc:  # noqa: BLE001
        from agent6_qualifier.alerts import notify_error

        notify_error(
            "amo.create_lead.wa",
            str(exc),
            f"WA client chat_id={session.chat_id}, lead NOT created",
        )


async def _gated_auto_outreach(client, session, store, amo) -> None:
    if not agent7_live_outreach_enabled():
        print(
            f"[agent7] OWNER_CHECK_READY object={session.lead.preferred_object_id} "
            f"chat={session.chat_id} (AGENT7_LIVE_OUTREACH_ENABLED=false — suppressed)"
        )
        store.save(session)
        return
    from agent7_envoy.auto import auto_outreach

    await auto_outreach(client, session, store, amo)


def _default_qualifier() -> Qualifier:
    from agent6_qualifier import notion_store
    from agent6_qualifier.publication_mapping_store import NotionPublicationMappingStore

    publication_store = NotionPublicationMappingStore(notion_store.fetch_all_pages)
    return Qualifier(
        find_by_id=notion_store.find_by_object_id,
        fetch_all=notion_store.fetch_all_listings,
        find_by_tg_post=notion_store.find_by_tg_post,
        publication_store=publication_store,
    )


def _extract_lead_update(text: str, session: Session) -> dict:
    """Shared archive-parity extract (Telegram + WhatsApp)."""
    from agent6_qualifier.session_extract import extract_lead_update_for_session

    return extract_lead_update_for_session(text, session, use_llm=True)


async def process_whatsapp_client_turn(
    message: CanonicalInboundMessage,
    *,
    config: WazzupConfig | None = None,
    ownership: ConversationOwnershipState | None = None,
    store: SessionStore | None = None,
    event_store: Any = None,
    amo: Any | None = None,
    qualifier: Qualifier | None = None,
    transport: WazzupWhatsAppTransport | None = None,
    force_dry_run_send: bool | None = None,
) -> WhatsAppTurnResult:
    """Run shared Agent 6 orchestration for one WhatsApp inbound message."""
    from agent6_qualifier import brain
    from agent6_qualifier.alerts import notify_error, notify_manager
    from agent6_qualifier.client_handler import (
        ClientMessageTemplates,
        process_client_message,
    )
    from agent8_notary.booking_doc import generate_booking_doc
    from agent8_notary.service import process_confirmed_booking

    cfg = config or load_wazzup_config()
    session_store = store or default_session_store()
    wa = transport or WazzupWhatsAppTransport(cfg)
    q = qualifier or _default_qualifier()

    phone = message.phone
    session_chat_id = whatsapp_session_chat_id(phone, chat_id=message.chat_id)
    conversation_key = whatsapp_conversation_key(phone, chat_id=message.chat_id)
    state = ownership or ConversationOwnershipState(
        chat_id=message.chat_id or session_chat_id
    )

    result = WhatsAppTurnResult(
        conversation_key=conversation_key,
        session_chat_id=session_chat_id,
        processed=False,
        replied=False,
    )

    if message.direction == "outbound":
        result.notes.append("skipped outbound webhook row")
        return result

    crm_id = (message.crm_message_id or "").strip()
    if message.is_from_bot is True or crm_id.startswith("agent6-"):
        result.notes.append("OWN_BOT_OUTBOUND ignored")
        return result

    session_early = _get_or_load_session(session_store, session_chat_id, phone=phone)
    if amo is not None and session_early.amo_lead_id:
        try:
            from agent6_qualifier.handoff_control import sync_from_amo_lead

            sync_from_amo_lead(amo, session_early, state)
        except Exception:
            pass
    if session_early.human_handoff_active and state.bot_may_reply():
        from agent6_qualifier.handoff_control import activate_stop

        activate_stop(
            session_early, state,
            reason=state.reason or "session_human_handoff",
            lead_id=session_early.amo_lead_id,
        )

    if not state.bot_may_reply():
        result.notes.append(f"handoff/ownership block owner={state.owner.value}")
        result.handoff = True
        result.outbound_mode = "blocked"
        result.outbound_blocked_reason = state.block_reason() or "handoff"
        try:
            from agent6_qualifier.messaging.ownership_store import OwnershipStore

            OwnershipStore().save(state, phone=phone or "")
            session_store.save(session_early)
        except Exception:
            pass
        return result

    try:
        from agent6_qualifier.messaging.contact_role_hook import on_unknown_inbound

        on_unknown_inbound(
            phone=phone,
            text=message.text,
            source_hint="AGENT6_INBOUND",
            has_existing_client_session=True,
            persist=True,
        )
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"contact_role skipped: {type(exc).__name__}")

    outbound_box: dict[str, Any] = {
        "mode": "suppressed",
        "reason": "",
        "preview": "",
    }

    async def send_client_response(_event: Any, reply: str) -> None:
        outbound_box["preview"] = (reply or "")[:160]
        recipient = phone or message.chat_id
        req = prepare_outbound_request(
            recipient_chat_id=str(recipient or ""),
            text=reply or "",
            channel_id=cfg.channel_id,
        )
        if not cfg.phone_allowed_for_live(phone or message.chat_id):
            outbound_box["mode"] = "blocked"
            outbound_box["reason"] = "allowlist_blocked"
            result.notes.append("outbound blocked: allowlist")
            return

        allow_live = cfg.send_enabled and cfg.auto_reply_enabled
        dry = True
        if force_dry_run_send is True:
            dry = True
        elif force_dry_run_send is False and allow_live:
            dry = False
        elif allow_live:
            dry = False
        else:
            outbound_box["mode"] = "dry_run"
            outbound_box["reason"] = (
                "send/auto_reply disabled — business processed, POST suppressed"
            )
            dry = True

        try:
            assert_bot_may_send(state)
            send_text_guarded(
                wa,
                req,
                ownership=state,
                store=event_store,
                dry_run=dry,
            )
            outbound_box["mode"] = "live" if not dry else "dry_run"
            if not dry and req.crm_message_id:
                state.known_bot_outbound_ids.add(req.crm_message_id)
        except PermissionError as exc:
            outbound_box["mode"] = "blocked"
            outbound_box["reason"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            outbound_box["mode"] = "blocked"
            outbound_box["reason"] = f"{type(exc).__name__}:{exc}"
            result.notes.append(f"send failed: {type(exc).__name__}")

    async def add_to_folder(*_a, **_k) -> None:
        return None

    async def process_confirmed_booking_wa(session, contact, **_kwargs):
        async def send_booking_doc(path):
            recipient = str(phone or message.chat_id or session.chat_id or "")
            if not cfg.phone_allowed_for_live(recipient):
                raise PermissionError("allowlist_blocked")
            allow_live = cfg.send_enabled and cfg.auto_reply_enabled
            dry = True
            if force_dry_run_send is True:
                dry = True
            elif force_dry_run_send is False and allow_live:
                dry = False
            elif allow_live:
                dry = False
            object_id = ""
            try:
                object_id = str(
                    getattr(session.lead, "preferred_object_id", "") or ""
                )
            except Exception:
                object_id = ""
            try:
                send_booking_document(
                    wa,
                    path=path,
                    recipient_chat_id=recipient,
                    ownership=state,
                    store=event_store,
                    object_id=object_id,
                    chat_id=str(session.chat_id or session_chat_id),
                    channel_id=cfg.channel_id,
                    dry_run=dry,
                )
                result.notes.append(
                    f"WA booking doc {'dry_run' if dry else 'sent'} "
                    f"path={getattr(path, 'name', path)}"
                )
            except DuplicateOutboundSuppressed:
                result.notes.append(
                    f"WA booking doc ALREADY_SENT path={getattr(path, 'name', path)}"
                )
                return None
            # Transport failure propagates to process_confirmed_booking
            # (sent_to_client=False) but does not rollback booking / amo attach.

        return await process_confirmed_booking(
            session,
            contact,
            generate_doc=generate_booking_doc,
            send_doc=send_booking_doc,
            amo_lead_id=session.amo_lead_id,
            attach_file=(amo.attach_file if amo is not None else None),
            on_generation_error=lambda e: notify_error(
                "booking_doc", str(e), "WA booking doc generation failed"
            ),
            on_attach_error=lambda e: notify_error(
                "amo.attach_file", str(e), "WA booking doc attach failed"
            ),
        )

    sender = SimpleNamespace(
        username="",
        phone=phone or "",
        first_name=message.sender_name or "",
        last_name="",
        channel="whatsapp",
    )
    event = _WaEvent(message.text or "", message.message_id)

    class _WaAgentClient:
        provider = "wazzup"

        async def send_whatsapp_text(self, to_phone: str, text: str) -> None:
            recipient = to_phone or phone or message.chat_id
            req = prepare_outbound_request(
                recipient_chat_id=str(recipient or ""),
                text=text or "",
                channel_id=cfg.channel_id,
            )
            if not cfg.phone_allowed_for_live(str(recipient or "")):
                raise PermissionError("allowlist_blocked")
            allow_live = cfg.send_enabled and cfg.auto_reply_enabled
            dry = not allow_live
            if force_dry_run_send is True:
                dry = True
            elif force_dry_run_send is False and allow_live:
                dry = False
            assert_bot_may_send(state)
            send_text_guarded(
                wa,
                req,
                ownership=state,
                store=event_store,
                dry_run=dry,
            )
            if not dry and req.crm_message_id:
                state.known_bot_outbound_ids.add(req.crm_message_id)

    client = _WaAgentClient()

    def get_session(cid: str) -> Session:
        return _get_or_load_session(session_store, cid, phone=phone)

    def ensure_amo(amo_client, session, _sender) -> None:
        ensure_amo_lead_whatsapp(
            amo_client,
            session,
            phone=phone,
            display_name=message.sender_name,
        )

    turn_holder: dict[str, Any] = {}

    def handle_message(session: Session, text: str, update: dict):
        turn = q.handle_message(session, text, update)
        turn_holder["turn"] = turn
        return turn

    pending_tasks: list[asyncio.Task] = []

    def create_task(coro):
        task = asyncio.create_task(coro)
        pending_tasks.append(task)
        return task

    await process_client_message(
        event=event,
        sender=sender,
        client=client,
        amo=amo,
        chat_id=session_chat_id,
        store=session_store,
        outreach_inflight=_outreach_inflight,
        get_session=get_session,
        extract_lead_update=_extract_lead_update,
        handle_message=handle_message,
        polish_reply=brain.polish_reply,
        send_client_response=send_client_response,
        process_confirmed_booking=process_confirmed_booking_wa,
        generate_booking_doc=generate_booking_doc,
        add_to_folder=add_to_folder,
        ensure_amo_lead=ensure_amo,
        notify_manager=notify_manager,
        notify_error=notify_error,
        auto_outreach=_gated_auto_outreach,
        create_task=create_task,
        templates=ClientMessageTemplates(
            notary_caption="Договор / booking request",
            clients_folder="Клиент",
            amo_stage_owner_request="Запрос владельцу",
            amo_stage_booking_confirmed="Бронь подтверждена",
        ),
    )

    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)

    session = get_session(session_chat_id)
    turn = turn_holder.get("turn")
    result.processed = True
    result.replied = bool(outbound_box.get("preview"))
    result.reply_preview = str(outbound_box.get("preview") or "")[:160]
    result.outbound_mode = str(outbound_box.get("mode") or "suppressed")
    result.outbound_blocked_reason = str(outbound_box.get("reason") or "")
    result.need_owner_check = bool(getattr(turn, "need_owner_check", False))
    result.booking_confirmed = bool(
        getattr(turn, "booking_confirmed", False) or session.booking_confirmed
    )
    result.handoff = bool(session.handoff_to_human)
    result.amo_lead_id = session.amo_lead_id
    if result.need_owner_check and not agent7_live_outreach_enabled():
        result.owner_check_status = "OWNER_CHECK_READY_SUPPRESSED"
    elif result.need_owner_check:
        result.owner_check_status = "OWNER_CHECK_DISPATCHED"
    result.notes.append(
        f"session_persisted={session_store._path(session_chat_id).name}"
    )
    try:
        from agent6_qualifier.messaging.ownership_store import OwnershipStore

        OwnershipStore().save(state, phone=phone or "")
    except Exception:  # noqa: BLE001
        result.notes.append("ownership_store_persist_failed")
    try:
        from agent6_qualifier.messaging.e2e_report import record_client_turn

        asked = getattr(session, "asked_core", False)
        lead = session.lead
        from agent6_qualifier.slot_planner import plan_qualification

        plan = plan_qualification(session, session.chosen)
        qualification_complete = plan.mvc_ready or bool(
            session.chosen and session.lead.preferred_object_id and lead.check_in
        )
        record_client_turn(
            inbound_text=message.text or "",
            phone=phone,
            turn=result,
            session_summary={
                "asked_core": asked,
                "preferred_object_id": lead.preferred_object_id,
                "check_in": lead.check_in.isoformat() if lead.check_in else None,
                "check_out": lead.check_out.isoformat() if lead.check_out else None,
                "budget": lead.budget,
                "guests": lead.guests,
                "area": list(lead.districts or []),
                "awaiting_owner": session.awaiting_owner,
                "booking_confirmed": session.booking_confirmed,
                "qualification_complete": qualification_complete,
                "history_len": len(session.history or []),
            },
        )
    except Exception:  # noqa: BLE001
        pass
    return result


def resume_whatsapp_bot(
    ownership: ConversationOwnershipState,
) -> ConversationOwnershipState:
    """Explicit HUMAN_HANDOFF → BOT_ACTIVE."""
    return resume_bot(ownership)
