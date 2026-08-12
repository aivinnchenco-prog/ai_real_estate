"""Phone normalization for WhatsApp UI sync (E.164 with leading +)."""

from __future__ import annotations

import re

_NON_DIGIT_RE = re.compile(r"\D+")


def normalize_phone_e164(raw: str | None, *, default_cc: str = "66") -> str | None:
    """Normalize to E.164 with leading '+', e.g. +66625124002.

    Digits-only helper mirrors Agent 6 Wazzup normalizer, then adds '+'.
    """
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


def phone_search_variants(phone_e164: str) -> tuple[str, ...]:
    """Ordered search strings for WhatsApp Web contact lookup."""
    e164 = normalize_phone_e164(phone_e164)
    if not e164:
        return ()
    digits = e164.lstrip("+")
    variants: list[str] = [e164, digits]
    if digits.startswith("66") and len(digits) > 2:
        national = digits[2:]
        variants.append("0" + national)
        # WhatsApp UI often shows: +66 62 512 4001
        if len(national) == 9:
            spaced = f"+66 {national[0:2]} {national[2:5]} {national[5:9]}"
            variants.append(spaced)
            variants.append(f"{national[0:2]} {national[2:5]} {national[5:9]}")
            variants.append(national)
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return tuple(out)


def digits_only(raw: str | None) -> str:
    return _NON_DIGIT_RE.sub("", str(raw or ""))


def phones_digit_equal(a: str | None, b: str | None) -> bool:
    da = normalize_phone_digits(a) or digits_only(a)
    db = normalize_phone_digits(b) or digits_only(b)
    return bool(da and db and da == db)
