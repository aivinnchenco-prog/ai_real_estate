"""WhatsApp outbound for Agent 7/8 owner outreach.

Production: Wazzup24 (WAZZUP_* in /opt/openhome/.env).
Green API (WA_INSTANCE_ID) — legacy fallback only.
"""

from __future__ import annotations

import json
import os
import re
import uuid

import requests


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def normalize_chat_id(phone: str) -> str:
    """Green API chatId (legacy)."""
    digits = _digits(phone)
    if not digits:
        raise ValueError("empty phone")
    if digits.startswith("0") and len(digits) >= 9:
        digits = "66" + digits[1:]
    return f"{digits}@c.us"


def normalize_wazzup_chat_id(phone: str) -> str:
    """Wazzup chatId: international digits without +."""
    digits = _digits(phone)
    if not digits:
        raise ValueError("empty phone")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) >= 9:
        digits = "66" + digits[1:]
    return digits


def wazzup_configured() -> bool:
    return bool(os.getenv("WAZZUP_API_KEY", "").strip())


def greenapi_configured() -> bool:
    provider = (os.getenv("WA_PROVIDER") or "").strip().lower()
    if provider and provider != "greenapi":
        return False
    return bool(os.getenv("WA_INSTANCE_ID") and os.getenv("WA_API_TOKEN"))


def configured() -> bool:
    provider = (os.getenv("WA_PROVIDER") or "").strip().lower()
    if provider == "wazzup" or (wazzup_configured() and provider != "greenapi"):
        return wazzup_configured() and _env_bool("WAZZUP_SEND_ENABLED", False)
    if provider == "greenapi" or greenapi_configured():
        return greenapi_configured()
    return wazzup_configured() and _env_bool("WAZZUP_SEND_ENABLED", False)


def _send_wazzup(phone: str, text: str) -> tuple[bool, str]:
    api_key = os.environ["WAZZUP_API_KEY"].strip()
    if not _env_bool("WAZZUP_SEND_ENABLED", False):
        return False, "wazzup_send_disabled"
    channel_id = (os.getenv("WAZZUP_CHANNEL_ID") or "").strip()
    if not channel_id:
        return False, "wazzup_channel_missing"
    base = (os.getenv("WAZZUP_API_BASE_URL") or "https://api.wazzup24.com").rstrip("/")
    chat_id = normalize_wazzup_chat_id(phone)
    payload = {
        "channelId": channel_id,
        "chatType": "whatsapp",
        "chatId": chat_id,
        "text": text,
        "crmMessageId": f"agent8-{uuid.uuid4()}",
    }
    try:
        r = requests.post(
            f"{base}/v3/message",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json=payload,
            timeout=float(os.getenv("WAZZUP_REQUEST_TIMEOUT_SECONDS", "15")),
        )
        if r.ok:
            return True, ""
        return False, f"wazzup_{r.status_code}"
    except Exception as exc:
        return False, str(exc)


def _send_greenapi(phone: str, text: str) -> tuple[bool, str]:
    instance = os.environ["WA_INSTANCE_ID"].strip()
    token = os.environ["WA_API_TOKEN"].strip()
    chat_id = normalize_chat_id(phone)
    url = f"https://api.green-api.com/waInstance{instance}/sendMessage/{token}"
    try:
        r = requests.post(
            url,
            json={"chatId": chat_id, "message": text},
            timeout=30,
        )
        if r.ok:
            return True, ""
        return False, f"greenapi_{r.status_code}"
    except Exception as exc:
        return False, str(exc)


def send_whatsapp(phone: str, text: str) -> tuple[bool, str]:
    """Send WhatsApp message to owner. Returns (ok, error_or_empty)."""
    provider = (os.getenv("WA_PROVIDER") or "").strip().lower()
    use_wazzup = provider == "wazzup" or (
        wazzup_configured() and provider != "greenapi"
    )
    if use_wazzup and wazzup_configured():
        return _send_wazzup(phone, text)
    if greenapi_configured():
        return _send_greenapi(phone, text)
    if wazzup_configured():
        return False, "wazzup_send_disabled"
    return False, "whatsapp_not_configured"
