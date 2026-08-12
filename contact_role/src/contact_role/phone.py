"""Contact identity helpers (E.164 + contact_key)."""

from __future__ import annotations

import re

_NON_DIGIT_RE = re.compile(r"\D+")


def normalize_phone_e164(raw: str | None, *, default_cc: str = "66") -> str | None:
    digits = normalize_phone_digits(raw, default_cc=default_cc)
    if not digits:
        return None
    return f"+{digits}"


def normalize_phone_digits(raw: str | None, *, default_cc: str = "66") -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    digits = _NON_DIGIT_RE.sub("", text)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if default_cc == "66":
        if digits.startswith("0") and len(digits) >= 9:
            digits = "66" + digits[1:]
        elif len(digits) == 9 and digits.startswith("6"):
            digits = "66" + digits
    return digits


def mask_phone(phone: str | None) -> str:
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if not digits:
        return "unknown"
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"+{digits[:2]}******{digits[-3:]}"


def contact_key_for(
    *,
    phone: str | None = None,
    contact_key: str | None = None,
    tg_username: str | None = None,
    tg_chat_id: str | None = None,
) -> str | None:
    if contact_key and str(contact_key).strip():
        return str(contact_key).strip()
    phone_n = normalize_phone_e164(phone)
    if phone_n:
        return f"wa:{phone_n}"
    tg = (tg_username or "").strip().lstrip("@").lower()
    if tg:
        return f"tg:{tg}"
    if tg_chat_id and str(tg_chat_id).strip():
        return f"tg_id:{str(tg_chat_id).strip()}"
    return None
