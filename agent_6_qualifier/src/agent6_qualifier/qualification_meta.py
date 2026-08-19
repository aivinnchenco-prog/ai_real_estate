"""Slot confidence layer (Wave 3) — compatibility side-car for ``LeadProfile``.

``LeadProfile`` keeps holding the values so every Wave 1/2 consumer is
untouched. This module tracks *how well we know* each value, stored on the
session as plain JSON (``session.slot_meta``) so old session files load and
new ones stay serialisable by ``dataclasses.asdict``.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import LeadProfile
    from .qualifier import Session


class Confidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class SlotSource(str, Enum):
    EXPLICIT_CLIENT = "explicit_client"
    INFERRED = "inferred"
    PUBLICATION = "publication"
    CRM = "crm"
    PREVIOUS_CONTEXT = "previous_context"


# Confidence ordering for "may this write win?" checks.
_RANK = {
    Confidence.UNKNOWN: 0,
    Confidence.INFERRED: 1,
    Confidence.CONFIRMED: 2,
}

# Sources that constitute an explicit client statement.
_EXPLICIT_SOURCES = frozenset({SlotSource.EXPLICIT_CLIENT})

TRACKED_SLOTS: tuple[str, ...] = (
    "check_in",
    "check_out",
    "stay_months",
    "budget",
    "districts",
    "bedrooms",
    "guests",
    "pets",
    "preferred_object_id",
)

_CORRECTION_HISTORY_LIMIT = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    return value


def lead_value(lead: LeadProfile, slot: str) -> Any:
    return getattr(lead, slot, None)


def is_set(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, str)):
        return len(value) > 0
    return True


def confidence_for_source(source: SlotSource | str) -> Confidence:
    """Deterministic source → confidence mapping (policy §2)."""
    src = SlotSource(source)
    if src in _EXPLICIT_SOURCES:
        return Confidence.CONFIRMED
    return Confidence.INFERRED


def get_meta(session: Session) -> dict[str, dict]:
    meta = getattr(session, "slot_meta", None)
    if not isinstance(meta, dict):
        meta = {}
        session.slot_meta = meta
    return meta


def slot_entry(session: Session, slot: str) -> dict | None:
    entry = get_meta(session).get(slot)
    return entry if isinstance(entry, dict) else None


def slot_confidence(session: Session, slot: str) -> Confidence:
    """Confidence for a slot, derived from the value when no meta exists.

    Sessions written before Wave 3 have no ``slot_meta``: an existing value is
    treated as INFERRED (known enough to skip re-asking, weak enough to be
    replaced by an explicit statement), a missing value as UNKNOWN.
    """
    entry = slot_entry(session, slot)
    if entry:
        try:
            return Confidence(entry.get("confidence") or Confidence.UNKNOWN.value)
        except ValueError:
            return Confidence.UNKNOWN
    if is_set(lead_value(session.lead, slot)):
        return Confidence.INFERRED
    return Confidence.UNKNOWN


def slot_source(session: Session, slot: str) -> SlotSource | None:
    entry = slot_entry(session, slot)
    if not entry:
        return None
    try:
        return SlotSource(entry.get("source") or "")
    except ValueError:
        return None


def is_known(session: Session, slot: str) -> bool:
    """CONFIRMED and INFERRED both count as known for re-ask prevention."""
    return slot_confidence(session, slot) != Confidence.UNKNOWN


def may_overwrite(
    session: Session,
    slot: str,
    new_source: SlotSource | str,
    *,
    is_correction: bool = False,
) -> bool:
    """CONFIRMED must not be silently replaced by an INFERRED value (policy §2)."""
    current = slot_confidence(session, slot)
    incoming = confidence_for_source(new_source)
    if current != Confidence.CONFIRMED:
        return True
    if is_correction:
        return True
    return _RANK[incoming] >= _RANK[current]


def record_slot(
    session: Session,
    slot: str,
    value: Any,
    source: SlotSource | str,
    *,
    is_correction: bool = False,
    message_snippet: str = "",
    at: str | None = None,
) -> dict:
    """Record confidence/source for a slot; appends to correction history."""
    meta = get_meta(session)
    src = SlotSource(source)
    entry = meta.get(slot) if isinstance(meta.get(slot), dict) else {}
    old_value = entry.get("value")
    new_value = _jsonable(value)

    history = list(entry.get("correction_history") or [])
    if is_correction and old_value != new_value:
        history.append({
            "at": at or _now_iso(),
            "old_value": old_value,
            "new_value": new_value,
            "message_snippet": (message_snippet or "")[:160],
        })
        history = history[-_CORRECTION_HISTORY_LIMIT:]

    updated = {
        "value": new_value,
        "confidence": confidence_for_source(src).value,
        "source": src.value,
        "updated_at": at or _now_iso(),
        "correction_history": history,
    }
    meta[slot] = updated
    return updated


def sync_from_update(
    session: Session,
    update: dict,
    *,
    message: str = "",
    corrected_slots: tuple[str, ...] | list[str] = (),
    explicit_slots: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    """Record meta for slots present in an extract result.

    ``explicit_slots`` marks slots the deterministic layer proved to be an
    explicit client statement; everything else coming from the LLM extract is
    INFERRED. Corrections always land as CONFIRMED.
    """
    lead = session.lead
    corrected = set(corrected_slots or ())
    explicit = set(explicit_slots) if explicit_slots is not None else set()
    touched: list[str] = []

    for slot in TRACKED_SLOTS:
        if slot not in (update or {}) and slot not in corrected:
            continue
        value = lead_value(lead, slot)
        if not is_set(value) and slot not in corrected:
            continue
        is_correction = slot in corrected
        source = (
            SlotSource.EXPLICIT_CLIENT
            if is_correction or slot in explicit
            else SlotSource.INFERRED
        )
        if not may_overwrite(session, slot, source, is_correction=is_correction):
            continue
        record_slot(
            session,
            slot,
            value,
            source,
            is_correction=is_correction,
            message_snippet=message,
        )
        touched.append(slot)
    return touched


def backfill_from_lead(session: Session, source: SlotSource = SlotSource.PREVIOUS_CONTEXT) -> None:
    """Give pre-Wave-3 sessions explicit meta without inventing confidence."""
    meta = get_meta(session)
    for slot in TRACKED_SLOTS:
        if slot in meta:
            continue
        value = lead_value(session.lead, slot)
        if is_set(value):
            record_slot(session, slot, value, source)


def clear_search_slots(session: Session) -> None:
    """Drop meta for search criteria on NEW_PROPERTY_SEARCH; identity is not tracked."""
    meta = get_meta(session)
    for slot in TRACKED_SLOTS:
        meta.pop(slot, None)


def confidence_snapshot(session: Session) -> dict[str, str]:
    return {slot: slot_confidence(session, slot).value for slot in TRACKED_SLOTS}
