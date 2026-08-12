"""Stable chat / message identity for amoCRM custom owner channels."""

from __future__ import annotations

import hashlib
import re
from typing import Any


_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")


def _slug(text: str, *, max_len: int = 48) -> str:
    s = _SAFE.sub("-", (text or "").strip())[:max_len].strip("-")
    return s or "x"


def stable_conversation_id(
    *,
    channel: str,
    object_id: str,
    external_thread_id: str = "",
    owner_request_id: str = "",
    source_url: str = "",
) -> str:
    """Deterministic amo conversation_id for one owner thread.

    Prefer external_thread_id; fall back to object+request hash.
    """
    ch = _slug(channel, max_len=12)
    oid = _slug(object_id, max_len=24)
    thread = (external_thread_id or "").strip()
    if thread:
        return f"oh-{ch}-{oid}-{_slug(thread, max_len=40)}"
    basis = "|".join(
        [
            channel or "",
            object_id or "",
            owner_request_id or "",
            source_url or "",
        ]
    )
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return f"oh-{ch}-{oid}-{digest}"


def stable_message_id(
    *,
    channel: str,
    direction: str,
    owner_request_id: str,
    external_message_id: str = "",
    text_fingerprint: str = "",
) -> str:
    """Stable msgid for amo import — retries must not mint a new id."""
    ch = _slug(channel, max_len=12)
    direction = (direction or "msg").strip().lower()
    if external_message_id:
        return f"oh-{ch}-{direction}-{_slug(external_message_id, max_len=48)}"
    basis = "|".join(
        [channel or "", direction, owner_request_id or "", text_fingerprint or ""]
    )
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20]
    return f"oh-{ch}-{direction}-{digest}"


def owner_participant_id(*, channel: str, conversation_id: str) -> str:
    return f"oh-owner-{_slug(channel, max_len=12)}-{hashlib.sha1(conversation_id.encode()).hexdigest()[:16]}"


def owner_display_name(channel: str, *, known_name: str = "") -> str:
    if (known_name or "").strip():
        return known_name.strip()[:80]
    key = (channel or "").lower()
    if "airbnb" in key:
        return "Airbnb Host"
    if "face" in key or "fb" in key:
        return "Facebook Marketplace Owner"
    return "Owner"


def bot_display_name() -> str:
    return "Open Home | Agent7"


def text_fingerprint(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]


def resolve_channel_key(channel: Any) -> str:
    text = str(getattr(channel, "value", channel) or "").strip().lower()
    if text in {"facebook_messenger", "fb_marketplace", "facebook", "fb"}:
        return "facebook"
    if text in {"airbnb_messages", "airbnb"}:
        return "airbnb"
    return text
