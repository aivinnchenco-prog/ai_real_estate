"""Deterministic conversation keys for Agent 6 (Telegram / WhatsApp)."""

from __future__ import annotations

from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits


def telegram_conversation_key(user_id: str | int) -> str:
    return f"telegram:{user_id}"


def whatsapp_conversation_key(phone: str | None, *, chat_id: str | None = None) -> str:
    digits = normalize_phone_e164_digits(phone or chat_id)
    if digits:
        return f"whatsapp:+{digits}"
    raw = (chat_id or phone or "unknown").strip()
    return f"whatsapp:{raw}"


def whatsapp_session_chat_id(phone: str | None, *, chat_id: str | None = None) -> str:
    """SessionStore-safe chat_id used by shared Qualifier/Session.

    Telegram keeps numeric Telethon ids.
    WhatsApp uses wa_<E164_digits> so files stay portable across restarts.
    """
    digits = normalize_phone_e164_digits(phone or chat_id)
    if digits:
        return f"wa_{digits}"
    safe = "".join(c for c in str(chat_id or phone or "unknown") if c.isalnum() or c in "-_")
    return f"wa_{safe or 'unknown'}"


def is_whatsapp_session_chat_id(chat_id: str | None) -> bool:
    return str(chat_id or "").startswith("wa_")
