"""Conversation ownership: human handoff TTL and bot silence rules."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qualifier import Session


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def handoff_ttl_hours() -> float:
    return float(os.getenv("AGENT6_HUMAN_HANDOFF_TTL_HOURS", "48"))


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _reference_human_activity(session: Session) -> datetime | None:
    for ts in (session.last_human_message_at, session.human_handoff_at):
        parsed = _parse_iso(ts)
        if parsed is not None:
            return parsed
    return None


def is_human_owned(session: Session) -> bool:
    """Manager/human owns the live conversation (TTL silence), not booking handoff flag."""
    return session.human_handoff_active


def human_handoff_expired(session: Session) -> bool:
    """TTL elapsed since last human/manager activity in human_handoff state."""
    if not session.human_handoff_active:
        return True
    ref = _reference_human_activity(session)
    if ref is None:
        # Active handoff without anchor — stay silent until explicit TTL data or resume.
        return False
    age_hours = (datetime.now(timezone.utc) - ref).total_seconds() / 3600.0
    return age_hours >= handoff_ttl_hours()


def should_bot_respond(session: Session) -> bool:
    """Bot may send an automatic reply to the client."""
    if not is_human_owned(session):
        return True
    return human_handoff_expired(session)


def activate_human_handoff(session: Session, at: str | None = None) -> None:
    """Manager/human intervened — bot stays silent until TTL expires."""
    ts = at or _now_iso()
    session.human_handoff_active = True
    session.human_handoff_at = ts
    session.last_human_message_at = ts


def touch_human_message(session: Session, at: str | None = None) -> None:
    """Record manager/human outbound activity (extends silence window)."""
    session.human_handoff_active = True
    if not session.human_handoff_at:
        session.human_handoff_at = at or _now_iso()
    session.last_human_message_at = at or _now_iso()


def touch_client_message(session: Session, at: str | None = None) -> None:
    session.last_client_message_at = at or _now_iso()
