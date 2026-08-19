"""Agent 7 owner WhatsApp outreach via Wazzup (independent of Telegram client)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.outbound_guard import (
    prepare_outbound_request,
    send_text_guarded,
)
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    assert_bot_may_send,
)
from agent6_qualifier.messaging.types import ConversationOwner
from agent6_qualifier.messaging.wa_client_runtime import agent7_live_outreach_enabled
from agent6_qualifier.messaging.wazzup_client import new_crm_message_id
from agent6_qualifier.messaging.wazzup_config import (
    load_wazzup_config,
    normalize_phone_e164_digits,
)
from agent6_qualifier.messaging.wazzup_transport import WazzupWhatsAppTransport

_DEFAULT_STORE = (
    Path(__file__).resolve().parents[2] / "data" / "wazzup_processed.sqlite"
)


def _event_store(path: Path | None = None) -> ProcessedEventStore:
    return ProcessedEventStore(path or _DEFAULT_STORE)


def send_owner_whatsapp_text(
    phone: str,
    text: str,
    *,
    dry_run: bool = False,
    event_store: ProcessedEventStore | None = None,
) -> dict[str, Any]:
    """Send first owner outreach over Wazzup. Used from Telegram and WhatsApp clients."""
    cfg = load_wazzup_config()
    digits = normalize_phone_e164_digits(phone)
    if not digits:
        raise ValueError("empty owner phone")
    if not agent7_live_outreach_enabled():
        raise PermissionError("AGENT7_LIVE_OUTREACH_ENABLED=false")
    if not cfg.send_enabled:
        raise PermissionError("WAZZUP_SEND_ENABLED=false")

    wa = WazzupWhatsAppTransport(cfg)
    store = event_store or _event_store()
    ownership = ConversationOwnershipState(
        chat_id=digits,
        owner=ConversationOwner.BOT_ACTIVE,
    )
    assert_bot_may_send(ownership)

    recipient = f"+{digits}"
    req = prepare_outbound_request(
        recipient_chat_id=recipient,
        text=text or "",
        crm_message_id=new_crm_message_id(prefix="agent7"),
        channel_id=cfg.channel_id,
    )
    result = send_text_guarded(
        wa,
        req,
        ownership=ownership,
        store=store,
        dry_run=dry_run,
    )
    if isinstance(result, dict):
        return result
    return {"ok": True, "dry_run": dry_run}
