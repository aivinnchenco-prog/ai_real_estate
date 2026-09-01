"""Deterministic qualification slot planner — Wave 2 progressive flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from .active_request import SourceFlow, resolve_source_flow
from .rental_policy import evaluate_rental_policy

if TYPE_CHECKING:
    from .models import Listing
    from .qualifier import Session


class Slot(str, Enum):
    CHECK_IN = "check_in"
    STAY_MONTHS = "stay_months"
    GUESTS = "guests"
    DISTRICTS = "districts"
    BUDGET = "budget"
    BEDROOMS = "bedrooms"
    PETS = "pets"


# Priority order when multiple slots missing (first asked first).
_OPEN_SEARCH_ORDER = (
    Slot.CHECK_IN,
    Slot.GUESTS,
    Slot.DISTRICTS,
    Slot.BUDGET,
    Slot.BEDROOMS,
    Slot.STAY_MONTHS,
    Slot.PETS,
)
_SPECIFIC_ORDER = (
    Slot.CHECK_IN,
    Slot.GUESTS,
    Slot.STAY_MONTHS,
    Slot.BEDROOMS,
    Slot.PETS,
)


@dataclass
class SlotPlan:
    source_flow: SourceFlow
    known: list[str] = field(default_factory=list)
    missing_critical: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    next_slots: list[Slot] = field(default_factory=list)
    mvc_ready: bool = False


def _slot_known(session: Session, slot: Slot) -> bool:
    lead = session.lead
    if slot == Slot.CHECK_IN:
        return lead.check_in is not None
    if slot == Slot.STAY_MONTHS:
        return lead.stay_months is not None
    if slot == Slot.GUESTS:
        return lead.guests is not None
    if slot == Slot.DISTRICTS:
        return bool(lead.districts) or bool(getattr(lead, "any_district", False))
    if slot == Slot.BUDGET:
        return lead.budget is not None
    if slot == Slot.BEDROOMS:
        return lead.bedrooms is not None
    if slot == Slot.PETS:
        return lead.pets is not None
    return False


def _slot_label(slot: Slot) -> str:
    return {
        Slot.CHECK_IN: "даты заезда",
        Slot.STAY_MONTHS: "срок проживания",
        Slot.GUESTS: "количество гостей",
        Slot.DISTRICTS: "район",
        Slot.BUDGET: "бюджет",
        Slot.BEDROOMS: "спальни",
        Slot.PETS: "питомцы",
    }[slot]


def _has_date_window(session: Session) -> bool:
    lead = session.lead
    return lead.check_in is not None or (
        lead.stay_months is not None and float(lead.stay_months) > 0
    )


def _open_search_mvc_ready(session: Session) -> bool:
    lead = session.lead
    if not _has_date_window(session):
        return False
    if lead.guests is None:
        return False
    if not lead.districts and not getattr(lead, "any_district", False):
        return False
    return True


def _specific_mvc_ready(session: Session, chosen: Listing | None) -> bool:
    lead = session.lead
    if lead.check_in is None:
        return False
    policy = evaluate_rental_policy(chosen, lead)
    if policy.needs_duration_clarification:
        return False
    return True


def plan_qualification(session: Session, chosen: Listing | None = None) -> SlotPlan:
    """Return known/missing slots and whether MVC is satisfied for next action."""
    flow = resolve_source_flow(session)
    order = (
        _SPECIFIC_ORDER if flow == SourceFlow.SPECIFIC_OBJECT else _OPEN_SEARCH_ORDER
    )

    known: list[str] = []
    missing_critical: list[str] = []
    missing_optional: list[str] = []

    for slot in Slot:
        if _slot_known(session, slot):
            known.append(_slot_label(slot))

    for slot in order:
        if not _slot_known(session, slot):
            label = _slot_label(slot)
            if flow == SourceFlow.SPECIFIC_OBJECT:
                if slot in (Slot.CHECK_IN, Slot.STAY_MONTHS):
                    missing_critical.append(label)
                elif slot == Slot.GUESTS:
                    missing_optional.append(label)
                elif slot in (Slot.DISTRICTS, Slot.BUDGET):
                    continue  # not required for specific object flow
                else:
                    missing_optional.append(label)
            else:
                if slot in (Slot.CHECK_IN, Slot.GUESTS, Slot.DISTRICTS):
                    missing_critical.append(label)
                elif slot in (Slot.BUDGET, Slot.BEDROOMS):
                    missing_optional.append(label)
                else:
                    missing_optional.append(label)

    next_slots: list[Slot] = []
    for slot in order:
        if not _slot_known(session, slot):
            if flow == SourceFlow.SPECIFIC_OBJECT and slot in (Slot.DISTRICTS, Slot.BUDGET):
                continue
            next_slots.append(slot)
            if len(next_slots) >= 2:
                break

    if flow == SourceFlow.SPECIFIC_OBJECT:
        mvc = _specific_mvc_ready(session, chosen)
    elif flow == SourceFlow.OPEN_SEARCH:
        mvc = _open_search_mvc_ready(session)
    else:
        mvc = False

    return SlotPlan(
        source_flow=flow,
        known=known,
        missing_critical=missing_critical,
        missing_optional=missing_optional,
        next_slots=next_slots,
        mvc_ready=mvc,
    )


def next_missing_slots(session: Session, chosen: Listing | None = None) -> SlotPlan:
    """Alias for policy doc naming."""
    return plan_qualification(session, chosen)
