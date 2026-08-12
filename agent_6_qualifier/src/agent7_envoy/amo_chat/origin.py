"""Message origin model + loop prevention for amo custom chat mirror."""

from __future__ import annotations

from enum import Enum


class MessageOrigin(str, Enum):
    SOURCE_NATIVE_INBOUND = "SOURCE_NATIVE_INBOUND"
    SOURCE_NATIVE_OUTBOUND = "SOURCE_NATIVE_OUTBOUND"
    AMO_MANAGER_OUTBOUND = "AMO_MANAGER_OUTBOUND"
    AMO_IMPORTED_MIRROR = "AMO_IMPORTED_MIRROR"


def should_send_to_source(origin: MessageOrigin | str) -> bool:
    """Only real manager outbound from amo may trigger source-native send."""
    value = origin.value if isinstance(origin, MessageOrigin) else str(origin)
    return value == MessageOrigin.AMO_MANAGER_OUTBOUND.value


def should_mirror_to_amo(origin: MessageOrigin | str) -> bool:
    """Imported amo echoes must never be re-imported; manager outbound already in amo."""
    value = origin.value if isinstance(origin, MessageOrigin) else str(origin)
    return value in {
        MessageOrigin.SOURCE_NATIVE_INBOUND.value,
        MessageOrigin.SOURCE_NATIVE_OUTBOUND.value,
    }


def classify_amo_webhook_message(
    *,
    msgid: str,
    conversation_id: str,
    already_imported: bool,
    sender_is_bot: bool,
    has_receiver: bool,
) -> MessageOrigin:
    """Classify amo webhook event for loop prevention.

    Heuristics from Chat API:
    - messages we imported carry our oh-* msgid prefix → AMO_IMPORTED_MIRROR
    - already_imported msgid → AMO_IMPORTED_MIRROR
    - manager outbound typically has receiver + non-bot sender
    """
    mid = (msgid or "").strip()
    if already_imported or mid.startswith("oh-"):
        return MessageOrigin.AMO_IMPORTED_MIRROR
    if sender_is_bot:
        return MessageOrigin.AMO_IMPORTED_MIRROR
    if has_receiver:
        return MessageOrigin.AMO_MANAGER_OUTBOUND
    # Inbound-looking webhook without our prefix — treat as mirror echo / ignore send
    return MessageOrigin.AMO_IMPORTED_MIRROR
