"""Client messaging transports for Agent 6 (Telegram + Wazzup WhatsApp)."""

from agent6_qualifier.messaging.types import (
    CanonicalInboundMessage,
    ClientMessagingTransport,
    ConversationOwner,
    DryRunSendResult,
    OutboundTextRequest,
)
from agent6_qualifier.messaging.wazzup_client import WazzupClient, new_crm_message_id
from agent6_qualifier.messaging.wazzup_config import (
    WazzupConfig,
    load_wazzup_config,
    normalize_phone_e164_digits,
)
from agent6_qualifier.messaging.wazzup_transport import (
    TelegramMessagingTransport,
    WazzupWhatsAppTransport,
)
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    assert_bot_may_send,
    apply_inbound_to_ownership,
    resume_bot,
)
from agent6_qualifier.messaging.crm_resolve import resolve_existing_crm_conversation
from agent6_qualifier.messaging.webhook import process_wazzup_webhook
from agent6_qualifier.messaging.inbound_dry_run import run_whatsapp_inbound_dry_run
from agent6_qualifier.messaging.wa_client_runtime import process_whatsapp_client_turn
from agent6_qualifier.messaging.conversation_key import (
    telegram_conversation_key,
    whatsapp_conversation_key,
    whatsapp_session_chat_id,
)

__all__ = [
    "CanonicalInboundMessage",
    "ClientMessagingTransport",
    "ConversationOwner",
    "ConversationOwnershipState",
    "DryRunSendResult",
    "OutboundTextRequest",
    "TelegramMessagingTransport",
    "WazzupClient",
    "WazzupConfig",
    "WazzupWhatsAppTransport",
    "apply_inbound_to_ownership",
    "assert_bot_may_send",
    "load_wazzup_config",
    "new_crm_message_id",
    "normalize_phone_e164_digits",
    "process_wazzup_webhook",
    "process_whatsapp_client_turn",
    "resolve_existing_crm_conversation",
    "resume_bot",
    "run_whatsapp_inbound_dry_run",
    "telegram_conversation_key",
    "whatsapp_conversation_key",
    "whatsapp_session_chat_id",
]
