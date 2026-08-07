"""Deterministic WhatsApp extraction and normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass

_WA_ME = re.compile(r"wa\.me/(\+?\d{7,15})", re.I)
_WA_LABEL = re.compile(r"(?:whatsapp|wa)\s*[:=]\s*([+\d][\d\s\-()]{6,20})", re.I)
_PLUS = re.compile(r"\+\d{7,15}")
_DIGITS = re.compile(r"\d[\d\s\-()]{6,20}\d|\d{7,15}")


@dataclass(frozen=True)
class WhatsAppParseResult:
    raw: str | None
    normalized: str | None
    valid: bool
    reason: str = ""


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


def normalize_thai_local(digits: str) -> str | None:
    """0812345678 -> +66812345678 only when unambiguous Thai mobile."""
    if digits.startswith("66") and len(digits) >= 11:
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 10 and digits[1] == "8":
        return f"+66{digits[1:]}"
    if digits.startswith("8") and len(digits) == 9:
        return f"+66{digits}"
    return None


def normalize_phone(raw: str) -> WhatsAppParseResult:
    text = (raw or "").strip()
    if not text:
        return WhatsAppParseResult(None, None, False, "empty")

    m = _WA_ME.search(text)
    if m:
        text = m.group(1)

    m = _WA_LABEL.search(text)
    if m:
        text = m.group(1)

    plus = _PLUS.search(text.replace(" ", ""))
    if plus:
        digits = _digits_only(plus.group(0))
        if 8 <= len(digits) <= 15:
            return WhatsAppParseResult(raw, f"+{digits}", True)

    digits = _digits_only(text)
    if not digits:
        return WhatsAppParseResult(raw, None, False, "no_digits")

    thai = normalize_thai_local(digits)
    if thai:
        return WhatsAppParseResult(raw, thai, True)

    if text.strip().startswith("+") and 8 <= len(digits) <= 15:
        return WhatsAppParseResult(raw, f"+{digits}", True)

    if 10 <= len(digits) <= 15 and not digits.startswith("0"):
        return WhatsAppParseResult(raw, f"+{digits}", True)

    return WhatsAppParseResult(raw, None, False, "ambiguous")


def extract_whatsapp_from_text(text: str) -> WhatsAppParseResult:
    if not text:
        return WhatsAppParseResult(None, None, False, "empty")
    for pattern in (_WA_ME, _WA_LABEL, _PLUS):
        m = pattern.search(text)
        if m:
            return normalize_phone(m.group(0) if pattern is not _PLUS else m.group(0))
    for m in _DIGITS.finditer(text):
        result = normalize_phone(m.group(0))
        if result.valid:
            return result
    return WhatsAppParseResult(text, None, False, "not_found")
