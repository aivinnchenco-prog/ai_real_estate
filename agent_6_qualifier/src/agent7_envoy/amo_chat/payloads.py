"""Build Chat API import payloads for owner custom channels."""

from __future__ import annotations

import time
from typing import Any

from agent7_envoy.amo_chat.identity import (
    bot_display_name,
    owner_display_name,
    owner_participant_id,
)


def build_inbound_owner_payload(
    *,
    conversation_id: str,
    msgid: str,
    text: str,
    channel: str,
    silent: bool = True,
    owner_name: str = "",
    msec_timestamp: int | None = None,
) -> dict[str, Any]:
    """Owner → amo (incoming). Only sender; no receiver. Prefer silent for no Неразобранное."""
    ts = int(time.time())
    msec = msec_timestamp or ts * 1000
    return {
        "timestamp": ts,
        "msec_timestamp": msec,
        "msgid": msgid,
        "conversation_id": conversation_id,
        "silent": bool(silent),
        "sender": {
            "id": owner_participant_id(channel=channel, conversation_id=conversation_id),
            "name": owner_display_name(channel, known_name=owner_name),
        },
        "message": {"type": "text", "text": text or ""},
    }


def build_outbound_bot_payload(
    *,
    conversation_id: str,
    msgid: str,
    text: str,
    channel: str,
    bot_id: str,
    silent: bool = True,
    owner_name: str = "",
    msec_timestamp: int | None = None,
) -> dict[str, Any]:
    """Agent7 bot → owner (outgoing). sender=bot ref_id, receiver=owner."""
    ts = int(time.time())
    msec = msec_timestamp or ts * 1000
    owner_id = owner_participant_id(channel=channel, conversation_id=conversation_id)
    sender: dict[str, Any] = {
        "id": bot_id or f"oh-bot-{channel}",
        "name": bot_display_name(),
    }
    if bot_id:
        sender["ref_id"] = bot_id
    return {
        "timestamp": ts,
        "msec_timestamp": msec,
        "msgid": msgid,
        "conversation_id": conversation_id,
        "silent": bool(silent),
        "sender": sender,
        "receiver": {
            "id": owner_id,
            "name": owner_display_name(channel, known_name=owner_name),
        },
        "message": {"type": "text", "text": text or ""},
    }
