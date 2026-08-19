"""Safe outbound send helpers (idempotency + ownership gates)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    assert_bot_may_send,
)
from agent6_qualifier.messaging.types import (
    DryRunSendResult,
    OutboundDocumentRequest,
    OutboundTextRequest,
)
from agent6_qualifier.messaging.wazzup_client import new_crm_message_id
from agent6_qualifier.messaging.wazzup_errors import (
    WazzupError,
    WazzupExternalAutoresponseNotConfirmed,
    WazzupSendDisabled,
)
from agent6_qualifier.messaging.wazzup_transport import WazzupWhatsAppTransport


class DuplicateOutboundSuppressed(WazzupError):
    code = "WAZZUP_DUPLICATE_OUTBOUND"


def prepare_outbound_request(
    *,
    recipient_chat_id: str,
    text: str,
    crm_message_id: str | None = None,
    channel_id: str | None = None,
) -> OutboundTextRequest:
    return OutboundTextRequest(
        recipient_chat_id=recipient_chat_id,
        text=text,
        crm_message_id=crm_message_id or new_crm_message_id(),
        channel_id=channel_id,
    )


def prepare_outbound_document(
    *,
    recipient_chat_id: str,
    content_uri: str,
    filename: str = "",
    content_type: str = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    crm_message_id: str | None = None,
    channel_id: str | None = None,
    local_path: str = "",
) -> OutboundDocumentRequest:
    return OutboundDocumentRequest(
        recipient_chat_id=recipient_chat_id,
        content_uri=content_uri,
        filename=filename,
        content_type=content_type,
        crm_message_id=crm_message_id or new_crm_message_id(),
        channel_id=channel_id,
        local_path=local_path,
    )


def booking_document_crm_message_id(*, chat_id: str, filename: str) -> str:
    """Stable outbound id so one booking agreement is not POSTed twice."""
    safe_chat = "".join(c for c in str(chat_id) if c.isalnum() or c in "-_")[:48]
    safe_name = "".join(
        c for c in str(filename) if c.isalnum() or c in "-_."
    )[:64]
    return f"agent6-booking-doc-{safe_chat}-{safe_name}"


def _assert_live_outbound_gates(
    transport: WazzupWhatsAppTransport,
    *,
    recipient_chat_id: str,
) -> None:
    cfg = transport.config
    if not cfg.phone_allowed_for_live(recipient_chat_id):
        raise WazzupSendDisabled(
            "WAZZUP_LIVE_ALLOWLIST — recipient not allowlisted for live POST"
        )
    if cfg.stage_mode and not cfg.live_allowlist_enabled:
        raise WazzupSendDisabled(
            "WAZZUP_STAGE_MODE requires WAZZUP_LIVE_ALLOWLIST_ENABLED=true"
        )
    transport.bot_may_send()
    if not transport.config.send_enabled:
        raise WazzupSendDisabled(
            "WAZZUP_SEND_ENABLED=false — POST blocked before network"
        )
    if not transport.config.external_autoresponse_confirmed_off:
        raise WazzupExternalAutoresponseNotConfirmed(
            "EXTERNAL_AUTORESPONSE_NOT_CONFIRMED_OFF — live POST blocked"
        )


def send_text_guarded(
    transport: WazzupWhatsAppTransport,
    request: OutboundTextRequest,
    *,
    ownership: ConversationOwnershipState,
    store: ProcessedEventStore | None = None,
    dry_run: bool = False,
) -> dict[str, Any] | DryRunSendResult:
    """Block on ownership / send flags / allowlist; suppress duplicate crmMessageId."""
    assert_bot_may_send(ownership)

    crm_id = request.crm_message_id or new_crm_message_id()
    request = OutboundTextRequest(
        recipient_chat_id=request.recipient_chat_id,
        text=request.text,
        crm_message_id=crm_id,
        channel_id=request.channel_id,
        chat_type=request.chat_type,
    )

    if store is not None and store.has_outbound(crm_id):
        raise DuplicateOutboundSuppressed(
            f"crmMessageId already sent: {crm_id}"
        )

    if dry_run:
        return transport.dry_run_send(request)

    _assert_live_outbound_gates(
        transport, recipient_chat_id=request.recipient_chat_id
    )

    payload = transport.send_text(request)
    if store is not None:
        store.mark_outbound(
            crm_id,
            chat_id=request.recipient_chat_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        ownership.known_bot_outbound_ids.add(crm_id)
    return payload


def send_document_guarded(
    transport: WazzupWhatsAppTransport,
    request: OutboundDocumentRequest,
    *,
    ownership: ConversationOwnershipState,
    store: ProcessedEventStore | None = None,
    dry_run: bool = False,
) -> dict[str, Any] | DryRunSendResult:
    """Same guards as text; Wazzup payload uses contentUri (no text)."""
    assert_bot_may_send(ownership)

    crm_id = request.crm_message_id or new_crm_message_id()
    request = OutboundDocumentRequest(
        recipient_chat_id=request.recipient_chat_id,
        content_uri=request.content_uri,
        filename=request.filename,
        content_type=request.content_type,
        crm_message_id=crm_id,
        channel_id=request.channel_id,
        chat_type=request.chat_type,
        local_path=request.local_path,
    )

    if store is not None and store.has_outbound(crm_id):
        raise DuplicateOutboundSuppressed(
            f"crmMessageId already sent: {crm_id}"
        )

    if dry_run:
        return transport.dry_run_send_document(request)

    _assert_live_outbound_gates(
        transport, recipient_chat_id=request.recipient_chat_id
    )

    payload = transport.send_document(request)
    if store is not None:
        store.mark_outbound(
            crm_id,
            chat_id=request.recipient_chat_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        ownership.known_bot_outbound_ids.add(crm_id)
    return payload
