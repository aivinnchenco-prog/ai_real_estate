"""Normalize Wazzup webhook payloads → CanonicalInboundMessage.

Supports confirmed api.wazzup24.com v3 shape (messages[]).
Also accepts synthetic message.add-style fixtures marked as such.
Does not invent missing fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent6_qualifier.messaging.types import CanonicalInboundMessage
from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits
from agent6_qualifier.messaging.wazzup_errors import WazzupMalformedPayload


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        # unix seconds
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _redact_message_row(row: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "messageId",
        "channelId",
        "chatId",
        "chatType",
        "direction",
        "status",
        "dateTime",
        "timestamp",
        "crmMessageId",
        "isEcho",
    }
    out = {k: row.get(k) for k in keep if k in row}
    if row.get("text") is not None:
        out["text_len"] = len(str(row.get("text") or ""))
    contact = row.get("contact")
    if isinstance(contact, dict):
        out["contact_keys"] = sorted(contact.keys())
    return out


def normalize_wazzup_message_row(
    row: dict[str, Any],
    *,
    raw_event_type: str = "messages",
) -> CanonicalInboundMessage | None:
    message_id = str(row.get("messageId") or row.get("message_id") or "").strip()
    if not message_id:
        return None
    chat_id = str(row.get("chatId") or row.get("chat_id") or "").strip()
    channel = str(row.get("chatType") or row.get("channel") or "whatsapp").lower()
    contact = row.get("contact") if isinstance(row.get("contact"), dict) else {}
    phone_raw = chat_id or (contact.get("phone") if contact else None)
    phone = normalize_phone_e164_digits(phone_raw)
    direction = str(row.get("direction") or "inbound").lower()
    sender_name = None
    if contact:
        sender_name = (
            contact.get("name")
            or contact.get("username")
            or contact.get("phone")
        )
        if sender_name is not None:
            sender_name = str(sender_name)
    attachments: list[dict[str, Any]] = []
    if row.get("contentUri") or row.get("content_uri"):
        attachments.append({"type": "content", "uri_present": True})

    is_from_bot: bool | None = None
    if "isEcho" in row:
        is_from_bot = bool(row.get("isEcho"))
    elif row.get("crmMessageId") and str(row.get("crmMessageId", "")).startswith("agent6-"):
        is_from_bot = True

    return CanonicalInboundMessage(
        provider="wazzup",
        channel=channel if channel else "whatsapp",
        message_id=message_id,
        chat_id=chat_id or (phone or ""),
        phone=phone,
        direction=direction,
        text=None if row.get("text") is None else str(row.get("text")),
        timestamp=_parse_ts(row.get("dateTime") or row.get("timestamp")),
        attachments=attachments,
        sender_name=sender_name,
        channel_id=str(row.get("channelId") or row.get("channel_id") or "") or None,
        raw_event_type=raw_event_type,
        is_from_bot=is_from_bot,
        crm_message_id=(
            str(row["crmMessageId"])
            if row.get("crmMessageId") is not None
            else None
        ),
        redacted_raw=_redact_message_row(row),
    )


def normalize_wazzup_webhook(payload: Any) -> list[CanonicalInboundMessage]:
    """Parse webhook JSON. Malformed → raise. Unsupported shape → empty list."""
    if payload is None:
        raise WazzupMalformedPayload("empty webhook body")
    if isinstance(payload, (bytes, bytearray)):
        import json

        try:
            payload = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WazzupMalformedPayload(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WazzupMalformedPayload("webhook body must be a JSON object")

    # Confirmed v3: {"messages": [ ... ]}
    if isinstance(payload.get("messages"), list):
        out: list[CanonicalInboundMessage] = []
        for row in payload["messages"]:
            if not isinstance(row, dict):
                continue
            msg = normalize_wazzup_message_row(row, raw_event_type="messages")
            if msg is not None:
                out.append(msg)
        return out

    # Synthetic / tech-partner-style message.add (fixture only unless confirmed live)
    if payload.get("event") == "message.add" or payload.get("type") == "message.add":
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            return []
        # Map snake_case fields if present
        mapped = {
            "messageId": data.get("messageId") or data.get("message_id"),
            "channelId": data.get("channelId") or data.get("channel_id"),
            "chatId": data.get("chatId") or data.get("chat_id"),
            "chatType": data.get("chatType") or data.get("chat_type") or "whatsapp",
            "direction": data.get("direction") or "inbound",
            "text": data.get("text"),
            "timestamp": data.get("timestamp") or data.get("dateTime"),
            "contact": data.get("contact") or data.get("sender"),
            "crmMessageId": data.get("crmMessageId") or data.get("crm_message_id"),
            "isEcho": data.get("isEcho"),
            "_fixture": data.get("_fixture") or payload.get("_fixture"),
        }
        msg = normalize_wazzup_message_row(mapped, raw_event_type="message.add")
        return [msg] if msg else []

    # statuses / other events — ignore safely
    if "statuses" in payload or "channels" in payload:
        return []
    return []
