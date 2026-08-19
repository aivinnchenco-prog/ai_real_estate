"""Safe structured log events for amo chat (no secrets / no message bodies)."""

from __future__ import annotations

import sys
from typing import Any


def amo_chat_event(event: str, **fields: Any) -> None:
    """Emit one searchable journal line. Never include secrets or raw message text."""
    blocked = {
        "secret",
        "token",
        "password",
        "authorization",
        "cookie",
        "text",
        "body",
        "payload",
        "channel_secret",
        "access_token",
        "refresh_token",
    }
    parts = [f"event={event}"]
    for key, value in fields.items():
        k = str(key).strip().lower()
        if k in blocked or any(b in k for b in blocked):
            continue
        raw = "" if value is None else str(value)
        raw = raw.replace("\n", " ").replace("\r", " ")[:120]
        parts.append(f"{key}={raw}")
    sys.stderr.write("[amo-chat] " + " ".join(parts) + "\n")
