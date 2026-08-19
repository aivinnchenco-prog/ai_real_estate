"""Shared booking-document outbound (R2 URL → Wazzup contentUri).

Telegram keeps Telethon send_file; WhatsApp uses this path via Agent8 send_doc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.outbound_guard import (
    DuplicateOutboundSuppressed,
    booking_document_crm_message_id,
    prepare_outbound_document,
    send_document_guarded,
)
from agent6_qualifier.messaging.ownership import ConversationOwnershipState
from agent6_qualifier.messaging.r2_booking_upload import (
    DOCX_CONTENT_TYPE,
    upload_booking_docx,
)
from agent6_qualifier.messaging.types import DryRunSendResult, OutboundDocumentRequest
from agent6_qualifier.messaging.wazzup_transport import WazzupWhatsAppTransport

UploadFn = Callable[..., str]


def send_booking_document(
    transport: WazzupWhatsAppTransport,
    *,
    path: Path | str,
    recipient_chat_id: str,
    ownership: ConversationOwnershipState,
    store: ProcessedEventStore | None = None,
    object_id: str = "",
    chat_id: str = "",
    channel_id: str | None = None,
    dry_run: bool = False,
    upload_fn: UploadFn | None = None,
    content_uri: str | None = None,
) -> dict[str, Any] | DryRunSendResult:
    """Upload DOCX (unless content_uri given) and send via guarded Wazzup document API.

    Duplicate crmMessageId → DuplicateOutboundSuppressed (caller may treat as ALREADY_SENT).
    """
    local = Path(path)
    filename = local.name if local.name else "booking.docx"
    crm_id = booking_document_crm_message_id(
        chat_id=chat_id or recipient_chat_id,
        filename=filename,
    )
    uri = (content_uri or "").strip()
    if not uri:
        if dry_run:
            # No R2 / network in dry-run; Wazzup POST is not executed.
            uri = f"https://example.invalid/dry-run-booking/{filename}"
        else:
            uploader = upload_fn or upload_booking_docx
            uri = uploader(
                local,
                object_id=object_id or "booking",
                chat_id=chat_id or recipient_chat_id,
            )
    request = prepare_outbound_document(
        recipient_chat_id=str(recipient_chat_id),
        content_uri=uri,
        filename=filename,
        content_type=DOCX_CONTENT_TYPE,
        crm_message_id=crm_id,
        channel_id=channel_id,
        local_path=str(local),
    )
    return send_document_guarded(
        transport,
        request,
        ownership=ownership,
        store=store,
        dry_run=dry_run,
    )


__all__ = [
    "DOCX_CONTENT_TYPE",
    "DuplicateOutboundSuppressed",
    "OutboundDocumentRequest",
    "send_booking_document",
]
