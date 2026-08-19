"""WhatsApp inbound → shared Agent 6 qualification → DRY-RUN outbound (no POST).

Uses the same Qualifier.handle_message core as Telegram. Never sends via Wazzup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from agent6_qualifier.messaging.crm_resolve import (
    CrmConversationResolution,
    resolve_existing_crm_conversation,
)
from agent6_qualifier.messaging.outbound_guard import prepare_outbound_request
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    assert_bot_may_send,
)
from agent6_qualifier.messaging.phone_mask import mask_phone
from agent6_qualifier.messaging.types import (
    CanonicalInboundMessage,
    ConversationOwner,
    DryRunSendResult,
)
from agent6_qualifier.messaging.wazzup_config import WazzupConfig, load_wazzup_config
from agent6_qualifier.messaging.wazzup_transport import WazzupWhatsAppTransport
from agent6_qualifier.qualifier import Qualifier, Session, Turn

logger = logging.getLogger(__name__)


@dataclass
class InboundDryRunResult:
    inbound_received: bool
    provider: str = ""
    channel: str = ""
    message_id: str = ""
    masked_phone: str = ""
    text_preview: str = ""
    ownership: str = ConversationOwner.BOT_ACTIVE.value
    qualification_invoked: bool = False
    proposed_reply_generated: bool = False
    proposed_reply_preview: str = ""
    qualification_state: str = ""
    dry_run: DryRunSendResult | None = None
    post_attempted: bool = False
    real_send: bool = False
    crm: CrmConversationResolution | None = None
    suppressed_reason: str = ""
    notes: list[str] = field(default_factory=list)

    def to_log_lines(self) -> list[str]:
        lines = [
            "INBOUND RECEIVED" if self.inbound_received else "INBOUND SKIPPED",
            f"provider={self.provider}",
            f"channel={self.channel}",
            f"message_id={self.message_id}",
            f"phone={self.masked_phone}",
            f'text="{self.text_preview}"',
            f"ownership={self.ownership}",
            f"Agent 6: conversation resolved; qualification_invoked={self.qualification_invoked}",
            f"qualification_state={self.qualification_state}",
            f"proposed_reply_generated={self.proposed_reply_generated}",
        ]
        if self.proposed_reply_preview:
            lines.append(f'proposed_reply_preview="{self.proposed_reply_preview}"')
        for note in self.notes:
            lines.append(f"note={note}")
        if self.crm is not None:
            lines.append(
                "CRM: "
                f"existing_contact={'YES' if self.crm.contact_id else 'NO'} "
                f"existing_lead={'YES' if self.crm.lead_id else 'NO'} "
                f"action={self.crm.action} "
                "duplicate_created=NO"
            )
        lines.append("OUTBOUND: DRY RUN")
        lines.append("send=false")
        lines.append("POST=NO")
        if self.dry_run is not None:
            lines.append(
                f"would_send provider={self.dry_run.provider} "
                f"channel_id={self.dry_run.channel_id} "
                f"recipient={mask_phone(self.dry_run.recipient)} "
                f"text_length={self.dry_run.text_length} "
                f"crm_message_id={self.dry_run.crm_message_id}"
            )
        if self.suppressed_reason:
            lines.append(f"suppressed={self.suppressed_reason}")
        return lines


def _default_qualifier() -> Qualifier:
    return Qualifier(
        find_by_id=lambda _oid: None,
        fetch_all=lambda: [],
        find_by_tg_post=None,
    )


def run_whatsapp_inbound_dry_run(
    message: CanonicalInboundMessage,
    *,
    config: WazzupConfig | None = None,
    qualifier: Qualifier | None = None,
    session: Session | None = None,
    ownership: ConversationOwnershipState | None = None,
    amo: Any | None = None,
    extract_lead_update: Callable[[str, Session], dict] | None = None,
    transport: WazzupWhatsAppTransport | None = None,
) -> InboundDryRunResult:
    """Process one inbound WhatsApp message through shared qualification, dry-run only."""
    cfg = config or load_wazzup_config()
    result = InboundDryRunResult(
        inbound_received=True,
        provider=message.provider,
        channel=message.channel,
        message_id=message.message_id,
        masked_phone=mask_phone(message.phone or message.chat_id),
        text_preview=(message.text or "")[:120],
        post_attempted=False,
        real_send=False,
    )

    if message.direction == "outbound":
        result.inbound_received = False
        result.suppressed_reason = "outbound_event"
        result.notes.append("skipped outbound webhook row")
        return result

    expected_channel = cfg.channel_id
    if message.channel_id and message.channel_id != expected_channel:
        result.inbound_received = False
        result.suppressed_reason = "wrong_channel"
        result.notes.append(
            f"ignored channel_id={message.channel_id} (expected {expected_channel})"
        )
        return result

    chat_key = message.chat_id or message.phone or message.message_id
    sess = session or Session(chat_id=f"wa:{chat_key}")
    state = ownership or ConversationOwnershipState(
        chat_id=chat_key,
        owner=ConversationOwner.BOT_ACTIVE,
    )
    if sess.handoff_to_human:
        state.owner = ConversationOwner.HUMAN_HANDOFF
        state.manager_takeover = True
        state.reason = "session.handoff_to_human"
    result.ownership = state.owner.value

    crm = resolve_existing_crm_conversation(
        amo,
        phone=message.phone,
        chat_id=message.chat_id,
    )
    result.crm = crm
    if crm.lead_id and sess.amo_lead_id is None:
        sess.amo_lead_id = crm.lead_id
        result.notes.append("reused existing amo lead — no duplicate create")
    elif crm.contact_id and crm.action == "reuse_existing":
        result.notes.append("existing contact+lead reused")
    else:
        result.notes.append(
            "CRM create deferred in dry-run (Wazzup↔amo already syncs chats)"
        )

    try:
        assert_bot_may_send(state)
    except PermissionError as exc:
        result.suppressed_reason = str(exc)
        result.qualification_invoked = False
        result.notes.append("HUMAN_HANDOFF/PAUSED/CLOSED — no active send path")
        return result

    if cfg.auto_reply_enabled:
        result.notes.append(
            "WAZZUP_AUTO_REPLY_ENABLED=true but send still gated; dry-run only"
        )

    # Canonical role router / auto-assign (fail-safe; never blocks dry-run path).
    try:
        import sys
        from pathlib import Path

        _root = Path(__file__).resolve().parents[4]
        _cr = _root / "contact_role" / "src"
        if str(_cr) not in sys.path:
            sys.path.insert(0, str(_cr))
        from contact_role.flags import auto_assign_enabled
        from contact_role.roles import CanonicalRole
        from contact_role.router import route_contact
        from agent6_qualifier.messaging.contact_role_hook import on_unknown_inbound

        if auto_assign_enabled():
            # Event-driven: known client lead OR classifier for UNKNOWN.
            # Persist only when auto-assign is on; still no Wazzup send here.
            existing_client = bool(
                getattr(sess, "amo_lead_id", None)
                or getattr(sess, "object_id", None)
                or getattr(sess, "asked_core", False)
            )
            has_publication_ref = False
            try:
                from agent6_qualifier.object_id import (
                    extract_object_ids,
                    extract_tg_post,
                    extract_utm_campaign,
                )

                body = message.text or ""
                has_publication_ref = bool(
                    extract_object_ids(body)
                    or extract_tg_post(body)
                    or extract_utm_campaign(body)
                )
            except Exception:
                has_publication_ref = False

            hook = on_unknown_inbound(
                phone=message.phone,
                text=message.text,
                known_client=False,
                source_hint="AGENT6_INBOUND" if (existing_client or has_publication_ref) else None,
                has_publication_ref=has_publication_ref,
                has_existing_client_session=existing_client,
                persist=True,
            )
            if isinstance(hook, dict) or hook is not None:
                payload = hook.to_dict() if hasattr(hook, "to_dict") else (
                    hook if isinstance(hook, dict) else {}
                )
                if payload:
                    result.notes.append(
                        f"auto_role={payload.get('canonical_role')} "
                        f"route={payload.get('route')} "
                        f"reason={payload.get('reason') or payload.get('event')}"
                    )
                    role = CanonicalRole.parse(payload.get("canonical_role"))
                    if role in {CanonicalRole.OWNER, CanonicalRole.AGENT}:
                        result.notes.append(
                            "OWNER/AGENT → Agent 7 path; Agent 6 qualification skipped"
                        )
                        result.qualification_invoked = False
                        result.suppressed_reason = f"routed_{payload.get('route')}"
                        return result
                elif hasattr(hook, "canonical_role"):
                    result.notes.append(
                        f"auto_role={hook.canonical_role} route={hook.route}"
                    )
                    role = CanonicalRole.parse(hook.canonical_role)
                    if role in {CanonicalRole.OWNER, CanonicalRole.AGENT}:
                        result.notes.append(
                            "OWNER/AGENT → Agent 7 path; Agent 6 qualification skipped"
                        )
                        result.qualification_invoked = False
                        result.suppressed_reason = f"routed_{hook.route}"
                        return result
        else:
            decision = route_contact(
                phone=message.phone,
                text=message.text,
                persist_workflow=False,
                persist_classification=False,
            )
            result.notes.append(
                f"canonical_role={decision.canonical_role} route={decision.route} "
                f"source={decision.role_source}"
            )
            if decision.used_existing and CanonicalRole.parse(
                decision.canonical_role
            ) in {CanonicalRole.OWNER, CanonicalRole.AGENT}:
                result.notes.append(
                    "OWNER/AGENT → Agent 7 path; Agent 6 qualification skipped for role"
                )
                result.qualification_invoked = False
                result.suppressed_reason = f"routed_{decision.route}"
                return result
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"contact_role router skipped: {type(exc).__name__}")

    q = qualifier or _default_qualifier()
    update: dict = {}
    if extract_lead_update is not None:
        try:
            update = extract_lead_update(message.text or "", sess) or {}
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"extract_lead_update failed: {type(exc).__name__}")
            update = {}

    turn: Turn = q.handle_message(sess, message.text or "", update)
    result.qualification_invoked = True
    result.proposed_reply_generated = bool((turn.reply_draft or "").strip())
    result.proposed_reply_preview = (turn.reply_draft or "")[:160]
    result.qualification_state = (
        f"asked_core={sess.asked_core};wants_selection={sess.wants_selection};"
        f"handoff={sess.handoff_to_human};amo_lead_id={sess.amo_lead_id}"
    )

    # Always dry-run — never POST regardless of send flag.
    wa = transport or WazzupWhatsAppTransport(cfg)
    req = prepare_outbound_request(
        recipient_chat_id=message.chat_id or (message.phone or ""),
        text=turn.reply_draft or "",
        channel_id=cfg.channel_id,
    )
    dry = wa.dry_run_send(req)
    result.dry_run = dry
    result.post_attempted = False
    result.real_send = False

    # Hard assert send remains blocked at config layer.
    if cfg.send_enabled:
        result.notes.append("WARNING: WAZZUP_SEND_ENABLED=true but dry-run path did not POST")
    logger.info("\n".join(result.to_log_lines()))
    return result
