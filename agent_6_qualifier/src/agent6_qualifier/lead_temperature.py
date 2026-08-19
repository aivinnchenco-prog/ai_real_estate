"""Lead temperature (Wave 3, policy §16) — internal metadata, never shown to the client.

Derived from observable runtime signals only; it never overrides a business
rule, it only annotates the lead for managers and nudges ``next_best_action``.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qualifier import Session


class LeadTemperature(str, Enum):
    COLD = "COLD"
    WARM = "WARM"
    HOT = "HOT"
    STALLED = "STALLED"


DEFAULT_STALLED_HOURS = 48.0

# Score thresholds (see _score_signals).
_HOT_THRESHOLD = 5
_WARM_THRESHOLD = 2

# Policy §16: HOT means the lead is moving on a deal, not merely well
# qualified. A full criteria set alone stays WARM until one of these appears.
_COMMITMENT_SIGNALS = frozenset({"booking_intent", "owner_check", "object_chosen"})


def stalled_after_hours() -> float:
    try:
        return float(os.getenv("AGENT6_LEAD_STALLED_HOURS", "") or DEFAULT_STALLED_HOURS)
    except ValueError:
        return DEFAULT_STALLED_HOURS


def _parse_iso(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours_since(ts: str) -> float | None:
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0


def _score_signals(session: Session) -> tuple[int, list[str]]:
    lead = session.lead
    score = 0
    signals: list[str] = []

    if session.booking_confirmed or session.booking_intent:
        score += 4
        signals.append("booking_intent")
    if session.awaiting_owner or session.owner_verdict:
        score += 3
        signals.append("owner_check")
    if session.chosen is not None or lead.preferred_object_id:
        score += 2
        signals.append("object_chosen")
    if lead.check_in is not None:
        score += 2
        signals.append("check_in")
    if lead.budget is not None:
        score += 1
        signals.append("budget")
    if lead.districts:
        score += 1
        signals.append("districts")
    if lead.guests is not None:
        score += 1
        signals.append("guests")
    if getattr(session, "liked_object_ids", None):
        score += 1
        signals.append("liked_object")
    if lead.full_name or lead.whatsapp:
        score += 1
        signals.append("contact_details")
    return score, signals


def is_stalled(session: Session) -> bool:
    """Previously active but gone quiet past the SLA window."""
    score, _ = _score_signals(session)
    if score < _WARM_THRESHOLD:
        return False
    age = _hours_since(getattr(session, "last_client_message_at", "") or "")
    if age is None:
        return False
    return age >= stalled_after_hours()


def compute_temperature(session: Session) -> LeadTemperature:
    if is_stalled(session):
        return LeadTemperature.STALLED
    score, signals = _score_signals(session)
    if score >= _HOT_THRESHOLD and _COMMITMENT_SIGNALS.intersection(signals):
        return LeadTemperature.HOT
    if score >= _WARM_THRESHOLD:
        return LeadTemperature.WARM
    return LeadTemperature.COLD


def refresh(session: Session) -> LeadTemperature:
    """Recompute and cache on the session (snapshot + handoff note read this)."""
    temperature = compute_temperature(session)
    session.lead_temperature = temperature.value
    return temperature


def explain(session: Session) -> list[str]:
    _, signals = _score_signals(session)
    return signals
