"""Manual-only human handoff: stop on human outbound / amo owner change; resume only via AI user."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .amo_tasks_config import default_responsible_user_id
from .messaging.ownership import ConversationOwnershipState, set_manager_takeover
from .messaging.types import ConversationOwner
from .session_ownership import activate_human_handoff


def handoff_manual_only() -> bool:
    raw = (os.getenv("AGENT6_HANDOFF_MANUAL_ONLY") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def ai_responsible_user_id() -> int | None:
    return default_responsible_user_id()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_handoff(
    action: str,
    *,
    reason: str,
    lead_id: int | None = None,
    pipeline_id: int | None = None,
) -> None:
    print(
        f"[handoff] {action} reason={reason} "
        f"lead_id={lead_id} pipeline_id={pipeline_id}",
        flush=True,
    )


def activate_stop(
    session,
    ownership: ConversationOwnershipState | None = None,
    *,
    reason: str,
    lead_id: int | None = None,
    pipeline_id: int | None = None,
) -> ConversationOwnershipState | None:
    """Stop the bot: session + ownership persist across restarts/webhooks."""
    activate_human_handoff(session)
    session.handoff_to_human = True
    if lead_id is not None:
        session.amo_lead_id = lead_id
    if ownership is not None:
        set_manager_takeover(ownership, enabled=True)
        ownership.reason = reason
        ownership.last_human_activity_at = (
            ownership.last_human_activity_at or _now_iso()
        )
    log_handoff(
        "STOP",
        reason=reason,
        lead_id=lead_id if lead_id is not None else getattr(session, "amo_lead_id", None),
        pipeline_id=pipeline_id,
    )
    return ownership


def resume_bot_manual(
    session,
    ownership: ConversationOwnershipState | None = None,
    *,
    reason: str = "responsible_back_to_ai",
    lead_id: int | None = None,
    pipeline_id: int | None = None,
) -> ConversationOwnershipState | None:
    """The only resume path in manual-only: responsible user returned to AI."""
    from .messaging.ownership import resume_bot

    session.human_handoff_active = False
    session.handoff_to_human = False
    if ownership is not None:
        resume_bot(ownership)
        ownership.reason = reason
    log_handoff(
        "START",
        reason=reason,
        lead_id=lead_id if lead_id is not None else getattr(session, "amo_lead_id", None),
        pipeline_id=pipeline_id,
    )
    return ownership


def persist_session_handoff(session) -> None:
    try:
        from .messaging.wa_client_runtime import default_session_store

        default_session_store().save(session)
    except Exception:
        try:
            from .sessions import SessionStore

            SessionStore(
                __import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "sessions"
            ).save(session)
        except Exception:
            pass


def persist_ownership(ownership: ConversationOwnershipState, *, phone: str = "") -> None:
    try:
        from .messaging.ownership_store import OwnershipStore

        OwnershipStore().save(ownership, phone=phone)
    except Exception:
        pass


def apply_external_outbound_stop(
    ownership: ConversationOwnershipState,
    *,
    session=None,
    reason: str,
    lead_id: int | None = None,
    pipeline_id: int | None = None,
    phone: str = "",
) -> ConversationOwnershipState:
    """C: any outbound not marked agent6-* is a human intervention."""
    set_manager_takeover(ownership, enabled=True)
    ownership.reason = reason
    if session is not None:
        activate_stop(
            session, ownership, reason=reason,
            lead_id=lead_id, pipeline_id=pipeline_id,
        )
    else:
        log_handoff("STOP", reason=reason, lead_id=lead_id, pipeline_id=pipeline_id)
    if phone:
        persist_ownership(ownership, phone=phone)
    return ownership


def apply_responsible_user_change(
    *,
    new_user_id: int | None,
    session,
    ownership: ConversationOwnershipState | None = None,
    lead_id: int | None = None,
    pipeline_id: int | None = None,
    previous_user_id: int | None = None,
) -> str:
    """A: amo responsible left AI → stop; returned to AI → resume. Only resume path."""
    ai_id = ai_responsible_user_id()
    if ai_id is None or new_user_id is None:
        return "skipped"
    prev = previous_user_id
    if prev is None:
        prev = getattr(session, "amo_responsible_user_id", None)
    session.amo_responsible_user_id = int(new_user_id)

    if int(new_user_id) != int(ai_id):
        if prev is None or int(prev) == int(ai_id) or not session.human_handoff_active:
            activate_stop(
                session, ownership,
                reason="amo_responsible_left_ai",
                lead_id=lead_id, pipeline_id=pipeline_id,
            )
            return "stop"
        return "already_stopped"

    if session.human_handoff_active or (
        ownership is not None
        and (ownership.manager_takeover or ownership.owner == ConversationOwner.HUMAN_HANDOFF)
    ):
        resume_bot_manual(
            session, ownership,
            reason="responsible_back_to_ai",
            lead_id=lead_id, pipeline_id=pipeline_id,
        )
        return "start"
    return "already_active"


def sync_from_amo_lead(
    amo: Any,
    session,
    ownership: ConversationOwnershipState | None = None,
) -> str:
    """GET lead responsible_user_id and apply stop/start."""
    lead_id = getattr(session, "amo_lead_id", None)
    if amo is None or not lead_id:
        return "no_lead"
    getter = getattr(amo, "get_lead", None)
    if callable(getter):
        lead = getter(lead_id)
    else:
        lead = amo._req("GET", f"/leads/{lead_id}")
    if not isinstance(lead, dict):
        return "no_lead"
    return apply_responsible_user_change(
        new_user_id=lead.get("responsible_user_id"),
        session=session,
        ownership=ownership,
        lead_id=int(lead.get("id") or lead_id),
        pipeline_id=lead.get("pipeline_id"),
        previous_user_id=getattr(session, "amo_responsible_user_id", None),
    )
