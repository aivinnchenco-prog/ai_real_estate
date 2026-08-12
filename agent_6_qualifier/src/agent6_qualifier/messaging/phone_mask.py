"""Phone masking for safe logs (never print full client numbers)."""

from __future__ import annotations


def mask_phone(phone: str | None) -> str:
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if not digits:
        return "unknown"
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"+{digits[:2]}******{digits[-3:]}"
