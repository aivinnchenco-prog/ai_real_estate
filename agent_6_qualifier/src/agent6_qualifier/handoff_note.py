"""Structured amoCRM handoff note (Wave 3, policy §15).

Adds a `[HANDOFF]` summary so a manager understands the case without reading
the chat. Existing notes are never replaced — this is an additional note.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qualifier import Session

HANDOFF_MARKER = "[HANDOFF]"

_EMPTY = "—"


def _fmt_list(values) -> str:
    items = [str(v) for v in (values or []) if str(v).strip()]
    return ", ".join(items) if items else _EMPTY


def _fmt(value) -> str:
    if value is None or value == "" or value == []:
        return _EMPTY
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _rejected_with_reasons(session: Session) -> str:
    reactions = getattr(session, "object_reactions", None) or []
    rejected = list(getattr(session, "rejected_object_ids", None) or [])
    if not rejected:
        return _EMPTY
    reason_by_object: dict[str, str] = {}
    for record in reactions:
        oid = record.get("object_id") or ""
        if oid in rejected:
            reason_by_object[oid] = record.get("reaction_type") or record.get("reason") or ""
    return ", ".join(
        f"{oid}: {reason_by_object.get(oid) or 'отклонён'}" for oid in rejected
    )


def _pending_owner(session: Session) -> str:
    items = getattr(session, "pending_owner_requests", None) or []
    parts: list[str] = []
    if session.awaiting_owner:
        oid = (
            session.chosen.object_id
            if session.chosen is not None
            else session.lead.preferred_object_id
        )
        if oid:
            parts.append(f"{oid}: awaiting_owner")
    for item in items:
        oid = item.get("object_id") or "?"
        status = (
            "awaiting_owner" if item.get("awaiting_owner")
            else (item.get("owner_verdict") or "нет ответа")
        )
        parts.append(f"{oid}: {status}")
    return ", ".join(parts) if parts else _EMPTY


def _last_client_message(session: Session) -> str:
    for entry in reversed(session.history or []):
        if entry.get("role") == "user":
            return (entry.get("text") or "").strip()[:300]
    return _EMPTY


def handoff_reason(session: Session, explicit: str = "") -> str:
    if explicit:
        return explicit
    if getattr(session, "repair_mode", False) and int(
        getattr(session, "misunderstanding_count", 0) or 0
    ) > 0:
        reason = getattr(session, "last_repair_reason", "") or "repair_failed"
        count = getattr(session, "misunderstanding_count", 0)
        return f"REPAIR_THRESHOLD_EXCEEDED ({reason}, попыток: {count})"
    if session.booking_confirmed:
        return "BOOKING_CONFIRMED (нужен менеджер для просмотра)"
    if session.human_handoff_active:
        return "CLIENT_REQUESTED_HUMAN"
    return "HANDOFF_REQUESTED"


def build_handoff_note(
    session: Session,
    *,
    reason: str = "",
    last_client_message: str = "",
) -> str:
    """Deterministic `[HANDOFF]` block for an amoCRM note."""
    from .active_request import active_request_summary, compute_next_best_action
    from .constraints import summary_lines
    from .lead_temperature import compute_temperature
    from .active_request import resolve_source_flow

    hard, soft = summary_lines(session)
    temperature = getattr(session, "lead_temperature", "") or compute_temperature(
        session
    ).value

    lines = [
        HANDOFF_MARKER,
        "",
        f"Intent: {resolve_source_flow(session).value}",
        f"Active request: {active_request_summary(session)}",
        f"Hard constraints: {_fmt_list(hard)}",
        f"Soft preferences: {_fmt_list(soft)}",
        f"Shown objects: {_fmt_list(getattr(session, 'shown_object_ids', None))}",
        f"Liked: {_fmt_list(getattr(session, 'liked_object_ids', None))}",
        f"Rejected: {_rejected_with_reasons(session)}",
        f"Pending owner checks: {_pending_owner(session)}",
        f"Lead temperature: {temperature}",
        f"Reason for handoff: {handoff_reason(session, reason)}",
        f"Next best action: {compute_next_best_action(session).value}",
        f"Last client message: {_fmt(last_client_message or _last_client_message(session))}",
    ]
    return "\n".join(lines)


def post_handoff_note(
    amo,
    session: Session,
    *,
    reason: str = "",
    last_client_message: str = "",
    notify_error=None,
) -> bool:
    """Append the structured note to the amo lead. Never raises."""
    lead_id = getattr(session, "amo_lead_id", None)
    if amo is None or not lead_id:
        return False
    note = build_handoff_note(
        session, reason=reason, last_client_message=last_client_message
    )
    object_id = session.lead.preferred_object_id or "-"
    try:
        amo.note_client(lead_id, object_id, note)
        return True
    except Exception as exc:  # amo outage must not block the client reply
        if notify_error is not None:
            notify_error(
                "amo.handoff_note",
                str(exc),
                f"структурированное примечание не записано в сделку #{lead_id}",
            )
        return False
