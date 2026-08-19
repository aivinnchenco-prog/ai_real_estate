"""Active search context reset without losing client identity / CRM history."""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from .intent import Intent
from .models import LeadProfile

if TYPE_CHECKING:
    from .qualifier import Session


def _listing_to_dict(listing) -> dict:
    if listing is None:
        return {}
    return dataclasses.asdict(listing)


def archive_active_owner_request(session: Session) -> None:
    """Move current owner-check into background pending list."""
    if not session.chosen and not session.lead.preferred_object_id:
        return
    if not (session.awaiting_owner or session.owner_verdict):
        return

    object_id = (
        session.chosen.object_id if session.chosen
        else session.lead.preferred_object_id
    )
    for item in session.pending_owner_requests:
        if item.get("object_id") == object_id and item.get("awaiting_owner"):
            return

    session.pending_owner_requests.append({
        "object_id": object_id,
        "awaiting_owner": session.awaiting_owner,
        "owner_verdict": session.owner_verdict,
        "chosen": _listing_to_dict(session.chosen),
        "check_in": session.lead.check_in.isoformat() if session.lead.check_in else "",
        "check_out": session.lead.check_out.isoformat() if session.lead.check_out else "",
        "guests": session.lead.guests,
    })


def _identity_fields(lead: LeadProfile) -> dict:
    return {
        "name": lead.name,
        "full_name": lead.full_name,
        "citizenship": lead.citizenship,
        "whatsapp": lead.whatsapp,
        "source_channel": lead.source_channel,
        "pets": lead.pets,
    }


def reset_lead_search_fields(lead: LeadProfile, update: dict) -> LeadProfile:
    """Clear search criteria; re-apply facts from current message."""
    identity = _identity_fields(lead)
    fresh = LeadProfile(**identity)
    from .brain import apply_update
    apply_update(fresh, update)
    return fresh


def _reset_wave3_search_memory(session: Session) -> None:
    """Wave 3 §19: reactions, constraints and confidence are search-scoped.

    Client identity, CRM binding and the repair counter live above the search
    and are deliberately left alone.
    """
    from .constraints import reset_constraints
    from .qualification_meta import clear_search_slots
    from .reactions import reset_reaction_memory

    reset_reaction_memory(session)
    reset_constraints(session)
    clear_search_slots(session)
    session.contradictions = []
    session.pending_clarification_slot = ""


def reset_search_context(
    session: Session,
    intent: Intent,
    update: dict,
) -> None:
    """Reset active search branch; keep client identity and CRM binding."""
    archive_active_owner_request(session)

    if intent == "NEW_PROPERTY_SEARCH":
        session.lead = reset_lead_search_fields(session.lead, update)
        session.chosen = None
        session.asked_object_source = False
        session.wants_selection = True
        session.asked_core = False
        session.asked_followup = False
        session.asked_checkout = False
        session.price_quoted = False
        session.offered_alternatives = False
        session.awaiting_alt_consent = False
        session.links_sent = False
        session.only_chosen = False
        session.awaiting_owner = False
        session.owner_verdict = ""
        session.booking_intent = False
        session.booking_confirmed = False
        session.last_outbound_template_key = ""
        _reset_wave3_search_memory(session)
    elif intent == "CHANGE_CRITERIA":
        from .brain import apply_update
        apply_update(session.lead, update)
        session.awaiting_owner = False
        session.owner_verdict = ""
        session.awaiting_alt_consent = False
        session.only_chosen = False
        session.price_quoted = False
        session.links_sent = False
        session.offered_alternatives = False
        if not update.get("preferred_object_id") and session.chosen is None:
            session.wants_selection = True
        session.last_outbound_template_key = ""


def find_pending_owner_request(session: Session, object_id: str) -> dict | None:
    for item in session.pending_owner_requests:
        if item.get("object_id") == object_id:
            return item
    return None


def apply_pending_verdict(session: Session, object_id: str, verdict_status: str) -> bool:
    """Update background owner request; does not touch active search state."""
    item = find_pending_owner_request(session, object_id)
    if item is None:
        return False
    item["awaiting_owner"] = False
    item["owner_verdict"] = verdict_status
    return True
