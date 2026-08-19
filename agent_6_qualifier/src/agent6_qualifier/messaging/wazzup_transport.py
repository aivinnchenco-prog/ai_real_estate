"""Wazzup WhatsApp transport + Telegram marker transport."""

from __future__ import annotations

import logging
from typing import Any

from agent6_qualifier.messaging.types import (
    CanonicalInboundMessage,
    ClientMessagingTransport,
    DryRunSendResult,
    OutboundDocumentRequest,
    OutboundTextRequest,
)
from agent6_qualifier.messaging.wazzup_client import WazzupClient, new_crm_message_id
from agent6_qualifier.messaging.wazzup_config import WazzupConfig, load_wazzup_config
from agent6_qualifier.messaging.wazzup_errors import (
    WazzupAutoReplyDisabled,
    WazzupChannelNotFound,
    WazzupExternalAutoresponseNotConfirmed,
    WazzupSendDisabled,
)
from agent6_qualifier.messaging.wazzup_inbound import normalize_wazzup_webhook

logger = logging.getLogger(__name__)


class TelegramMessagingTransport(ClientMessagingTransport):
    """Marker adapter — live Telegram remains Telethon userbot (unchanged)."""

    @property
    def provider_name(self) -> str:
        return "telegram"

    def healthcheck(self) -> dict[str, Any]:
        return {
            "ok": True,
            "provider": "telegram",
            "note": "Telegram runtime is Telethon userbot; not routed through this adapter",
        }

    def normalize_inbound(self, payload: Any) -> list[CanonicalInboundMessage]:
        return []

    def send_text(self, request: OutboundTextRequest) -> dict[str, Any]:
        raise RuntimeError(
            "TelegramMessagingTransport.send_text is not used — "
            "Telegram outbound stays in tg_userbot / humanized_respond"
        )


class WazzupWhatsAppTransport(ClientMessagingTransport):
    def __init__(
        self,
        config: WazzupConfig | None = None,
        *,
        client: WazzupClient | None = None,
    ):
        self.config = config or load_wazzup_config()
        self.client = client or WazzupClient(self.config)

    @property
    def provider_name(self) -> str:
        return "wazzup"

    def healthcheck(self) -> dict[str, Any]:
        return self.client.healthcheck()

    def normalize_inbound(self, payload: Any) -> list[CanonicalInboundMessage]:
        return normalize_wazzup_webhook(payload)

    def assert_expected_channel(self, channel: dict[str, Any] | None) -> None:
        if channel is None:
            raise WazzupChannelNotFound(
                f"channel {self.config.channel_id} not found"
            )
        transport = (channel.get("transport") or "").lower()
        state = (channel.get("state") or "").lower()
        plain = str(channel.get("plain_id") or "")
        if transport and transport != self.config.expected_transport:
            raise WazzupChannelNotFound(
                f"wrong transport {transport!r} (expected {self.config.expected_transport})"
            )
        if state and state != self.config.expected_state:
            raise WazzupChannelNotFound(
                f"channel state {state!r} (expected {self.config.expected_state})"
            )
        if plain and plain != self.config.expected_plain_id:
            raise WazzupChannelNotFound(
                f"plainId {plain!r} (expected {self.config.expected_plain_id})"
            )

    def dry_run_send(self, request: OutboundTextRequest) -> DryRunSendResult:
        return DryRunSendResult(
            would_send=True,
            provider=self.provider_name,
            channel_id=request.channel_id or self.config.channel_id,
            recipient=request.recipient_chat_id,
            text_length=len(request.text or ""),
            crm_message_id=request.crm_message_id,
            reason="dry-run (no network POST)",
        )

    def dry_run_send_document(
        self, request: OutboundDocumentRequest
    ) -> DryRunSendResult:
        return DryRunSendResult(
            would_send=True,
            provider=self.provider_name,
            channel_id=request.channel_id or self.config.channel_id,
            recipient=request.recipient_chat_id,
            text_length=0,
            crm_message_id=request.crm_message_id,
            reason="dry-run document (no network POST)",
            content_uri=request.content_uri,
            filename=request.filename,
        )

    def send_text(self, request: OutboundTextRequest) -> dict[str, Any]:
        if not self.config.send_enabled:
            raise WazzupSendDisabled(
                "WAZZUP_SEND_ENABLED=false — real WhatsApp send blocked"
            )
        if not self.config.auto_reply_enabled:
            # Defense in depth for Agent 6 bot replies; explicit tool send may still
            # require send_enabled alone — auto_reply gate is for qualification loop.
            logger.info(
                "Wazzup send called while WAZZUP_AUTO_REPLY_ENABLED=false "
                "(allowed only if caller is an explicit non-bot path)"
            )
        return self.client.send_text(
            chat_id=request.recipient_chat_id,
            text=request.text,
            crm_message_id=request.crm_message_id or new_crm_message_id(),
            channel_id=request.channel_id or self.config.channel_id,
            chat_type=request.chat_type or "whatsapp",
        )

    def send_document(self, request: OutboundDocumentRequest) -> dict[str, Any]:
        if not self.config.send_enabled:
            raise WazzupSendDisabled(
                "WAZZUP_SEND_ENABLED=false — real WhatsApp send blocked"
            )
        return self.client.send_document(
            chat_id=request.recipient_chat_id,
            content_uri=request.content_uri,
            crm_message_id=request.crm_message_id or new_crm_message_id(),
            channel_id=request.channel_id or self.config.channel_id,
            chat_type=request.chat_type or "whatsapp",
        )

    def bot_may_send(self) -> None:
        """Canonical live-send gate used by send_text_guarded before POST /v3/message."""
        if not self.config.send_enabled:
            raise WazzupSendDisabled("WAZZUP_SEND_ENABLED=false")
        if not self.config.auto_reply_enabled:
            raise WazzupAutoReplyDisabled(
                "WAZZUP_AUTO_REPLY_ENABLED=false — disable Wazzup/amoCRM autoresponse first"
            )
        if not self.config.external_autoresponse_confirmed_off:
            raise WazzupExternalAutoresponseNotConfirmed(
                "WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF=false — "
                "confirm WhatsApp Business / Wazzup / amoCRM auto-greetings are OFF "
                "before live Agent6 POST"
            )
