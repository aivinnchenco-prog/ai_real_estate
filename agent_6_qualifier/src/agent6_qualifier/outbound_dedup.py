"""Hard outbound anti-dupe: suppress a reply that matches recent bot messages."""
from __future__ import annotations

from .repair import _SIMILARITY_THRESHOLD, _last_bot_messages, _similar

OUTBOUND_DEDUP_N = 3


def should_suppress_outbound(
    session,
    reply: str,
    *,
    n: int = OUTBOUND_DEDUP_N,
    threshold: float = _SIMILARITY_THRESHOLD,
) -> bool:
    text = (reply or "").strip()
    if not text:
        return False
    for previous in _last_bot_messages(session, limit=n):
        if _similar(previous, text) >= threshold:
            return True
    return False
