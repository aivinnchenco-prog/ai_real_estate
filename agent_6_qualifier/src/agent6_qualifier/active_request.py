"""ACTIVE REQUEST snapshot and NEXT BEST ACTION — deterministic runtime truth."""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Listing
    from .qualifier import Session


class NextBestAction(str, Enum):
    ASK_CRITICAL_SLOT = "ASK_CRITICAL_SLOT"
    SHOW_MATCHES = "SHOW_MATCHES"
    REFINE_MATCHES = "REFINE_MATCHES"
    CHECK_OWNER = "CHECK_OWNER"
    WAIT_OWNER = "WAIT_OWNER"
    OFFER_ALTERNATIVES = "OFFER_ALTERNATIVES"
    START_BOOKING = "START_BOOKING"
    REPAIR_DIALOGUE = "REPAIR_DIALOGUE"
    HANDOFF_HUMAN = "HANDOFF_HUMAN"
    FOLLOW_UP = "FOLLOW_UP"
    NONE = "NONE"


class SourceFlow(str, Enum):
    SPECIFIC_OBJECT = "specific_object"
    OPEN_SEARCH = "open_search"
    UNKNOWN = "unknown"


def resolve_source_flow(session: Session) -> SourceFlow:
    if session.chosen is not None or session.lead.preferred_object_id:
        return SourceFlow.SPECIFIC_OBJECT
    if session.wants_selection:
        return SourceFlow.OPEN_SEARCH
    return SourceFlow.UNKNOWN


def build_active_request(session: Session) -> dict[str, Any]:
    """Structured snapshot over current Session — no LLM (Wave 3 V2)."""
    from .constraints import hard_constraints, soft_preferences
    from .contradictions import pending_contradictions
    from .lead_temperature import compute_temperature
    from .qualification_meta import confidence_snapshot

    lead = session.lead
    chosen_id = ""
    if session.chosen is not None:
        chosen_id = session.chosen.object_id
    elif lead.preferred_object_id:
        chosen_id = lead.preferred_object_id

    pending_ids = [
        p.get("object_id", "")
        for p in (session.pending_owner_requests or [])
        if p.get("object_id")
    ]

    shown = list(getattr(session, "shown_object_ids", None) or [])
    rejected = list(getattr(session, "rejected_object_ids", None) or [])
    liked = list(getattr(session, "liked_object_ids", None) or [])
    unresolved = [c for c in pending_contradictions(session) if not c.get("resolved")]

    return {
        "intent": resolve_source_flow(session).value,
        "source_flow": resolve_source_flow(session).value,
        "check_in": lead.check_in.isoformat() if lead.check_in else None,
        "check_out": lead.check_out.isoformat() if lead.check_out else None,
        "stay_months": lead.stay_months,
        "budget": lead.budget,
        "districts": list(lead.districts or []),
        "bedrooms": lead.bedrooms,
        "guests": lead.guests,
        "pets": lead.pets,
        "chosen_object_id": chosen_id or None,
        "shown_object_ids": shown,
        "rejected_object_ids": rejected,
        "liked_object_ids": liked,
        "pending_owner_request_ids": pending_ids,
        "awaiting_owner": session.awaiting_owner,
        "owner_verdict": session.owner_verdict or None,
        # Wave 3 additions
        "slot_confidence": confidence_snapshot(session),
        "hard_constraints": list(hard_constraints(session)),
        "soft_preferences": list(soft_preferences(session)),
        "positive_preferences": list(getattr(session, "positive_preferences", None) or []),
        "negative_preferences": list(getattr(session, "negative_preferences", None) or []),
        "contradictions": unresolved,
        "repair_mode": bool(getattr(session, "repair_mode", False)),
        "misunderstanding_count": int(getattr(session, "misunderstanding_count", 0) or 0),
        "lead_temperature": (
            getattr(session, "lead_temperature", "") or compute_temperature(session).value
        ),
        "next_best_action": compute_next_best_action(session).value,
    }


def compute_next_best_action(session: Session, chosen: Listing | None = None) -> NextBestAction:
    """Map runtime state to canonical action enum (Wave 3 V2)."""
    from .contradictions import has_unresolved
    from .slot_planner import plan_qualification

    if session.human_handoff_active or session.handoff_to_human:
        return NextBestAction.HANDOFF_HUMAN

    # Repair and unresolved conflicts outrank workflow steps: the agent must
    # first agree with the client on what is being searched for.
    if getattr(session, "repair_mode", False):
        return NextBestAction.REPAIR_DIALOGUE

    if has_unresolved(session):
        return NextBestAction.ASK_CRITICAL_SLOT

    if session.awaiting_owner and not session.owner_verdict:
        return NextBestAction.WAIT_OWNER

    if session.owner_verdict == "free" and not session.booking_confirmed:
        return NextBestAction.START_BOOKING

    plan = plan_qualification(session, chosen)

    if not plan.mvc_ready:
        return NextBestAction.ASK_CRITICAL_SLOT

    flow = resolve_source_flow(session)
    if flow == SourceFlow.OPEN_SEARCH and session.chosen is None:
        if getattr(session, "rejected_object_ids", None) and session.offered_alternatives:
            return NextBestAction.REFINE_MATCHES
        if session.offered_alternatives:
            return NextBestAction.REFINE_MATCHES
        return NextBestAction.SHOW_MATCHES

    if session.awaiting_alt_consent:
        return NextBestAction.OFFER_ALTERNATIVES

    if session.chosen is not None and not session.owner_verdict and not session.awaiting_owner:
        return NextBestAction.CHECK_OWNER

    return NextBestAction.NONE


def active_request_summary(session: Session) -> str:
    """One-line for knowledge / handoff notes."""
    snap = build_active_request(session)
    parts: list[str] = []
    if snap.get("chosen_object_id"):
        parts.append(f"объект {snap['chosen_object_id']}")
    if snap.get("districts"):
        parts.append(", ".join(snap["districts"]))
    if snap.get("budget"):
        parts.append(f"до {snap['budget']:,.0f} ฿".replace(",", " "))
    if snap.get("check_in"):
        parts.append(f"заезд {snap['check_in']}")
    if snap.get("guests"):
        parts.append(f"гостей {snap['guests']}")
    if snap.get("stay_months"):
        parts.append(f"{snap['stay_months']} мес")
    action = snap.get("next_best_action", "")
    if parts:
        return f"{'; '.join(parts)} → {action}"
    return f"запрос → {action}"
