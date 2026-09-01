"""Manual-only handoff: stop on human outbound (C) or amo tag; resume only via start tag."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .messaging.ownership import ConversationOwnershipState, set_manager_takeover
from .repair import _normalize, _similar
from .session_ownership import activate_human_handoff


def handoff_manual_only() -> bool:
    raw = (os.getenv("AGENT6_HANDOFF_MANUAL_ONLY") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def tag_stop_name() -> str:
    return (os.getenv("AGENT6_TAG_STOP") or "Бот: стоп").strip()


def tag_start_name() -> str:
    return (os.getenv("AGENT6_TAG_START") or "Бот: старт").strip()


def tag_cache_sec() -> float:
    try:
        return max(0.0, float(os.getenv("AGENT6_TAG_CACHE_SEC") or "10"))
    except ValueError:
        return 10.0


def greeting_allowlist() -> list[str]:
    raw = os.getenv("AGENT6_GREETING_ALLOWLIST") or ""
    parts = []
    for chunk in raw.replace(";", "\n").replace(",", "\n").splitlines():
        item = _normalize(chunk)
        if item:
            parts.append(item)
    return parts


def is_greeting_allowlisted(text: str | None) -> bool:
    norm = _normalize(text or "")
    if not norm:
        return False
    for item in greeting_allowlist():
        if item in norm or _similar(item, norm) >= 0.86:
            return True
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def chat_is_paused(session, ownership: ConversationOwnershipState | None = None) -> bool:
    if getattr(session, "human_handoff_active", False):
        return True
    if ownership is None:
        return False
    return bool(ownership.manager_takeover or not ownership.bot_may_reply())


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
    session.last_pause_at = _now_iso()
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
    reason: str = "resume via tag",
    lead_id: int | None = None,
    pipeline_id: int | None = None,
) -> ConversationOwnershipState | None:
    """The only resume path: amo tag «Бот: старт»."""
    from .messaging.ownership import resume_bot

    session.human_handoff_active = False
    session.handoff_to_human = False
    session.last_start_applied_at = _now_iso()
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
            from pathlib import Path

            from .sessions import SessionStore

            SessionStore(
                Path(__file__).resolve().parents[2] / "data" / "sessions"
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


def _tag_names_from_lead(lead: dict) -> list[str]:
    tags = (lead.get("_embedded") or {}).get("tags") or []
    return [str(t.get("name") or "").strip() for t in tags if t.get("name")]


def _cache_fresh(session) -> bool:
    checked = _parse_iso(getattr(session, "amo_tags_checked_at", "") or "")
    if checked is None:
        return False
    age = (datetime.now(timezone.utc) - checked).total_seconds()
    return age < tag_cache_sec()


def _should_honor_start(session, names: list[str], start: str) -> bool:
    """Honor «старт» once per appearance; hanging tag after C is ignored.

    After a pause, START is applied only if we saw it *absent* after
    ``last_pause_at`` (re-add). A leftover tag is older than
    ``last_start_applied_at`` and must not lift C.
    """
    if start not in names:
        return False
    if getattr(session, "amo_start_tag_present", False):
        return False
    last_start = _parse_iso(getattr(session, "last_start_applied_at", "") or "")
    last_pause = _parse_iso(getattr(session, "last_pause_at", "") or "")
    last_absent = _parse_iso(getattr(session, "last_start_absent_at", "") or "")
    if last_start is not None and last_pause is not None and last_pause >= last_start:
        if last_absent is None or last_absent < last_pause:
            return False
    return True


def clear_bot_control_tags(amo: Any, lead_id: int) -> bool:
    """Remove start/stop tags; keep every other tag (OBJ_* etc)."""
    start, stop = tag_start_name(), tag_stop_name()
    try:
        if hasattr(amo, "lead_tag_names") and hasattr(amo, "replace_lead_tags"):
            names = [n for n in amo.lead_tag_names(lead_id) if n not in (start, stop)]
            amo.replace_lead_tags(lead_id, names)
            return True
        lead = amo.get_lead(lead_id) if hasattr(amo, "get_lead") else amo._req(
            "GET", f"/leads/{int(lead_id)}?with=tags"
        )
        kept = [n for n in _tag_names_from_lead(lead or {}) if n not in (start, stop)]
        amo._req(
            "PATCH",
            f"/leads/{int(lead_id)}",
            json={"_embedded": {"tags": [{"name": n} for n in kept]}},
        )
        return True
    except Exception:
        return False


def apply_amo_tags(
    names: list[str],
    session,
    ownership: ConversationOwnershipState | None = None,
    *,
    lead_id: int | None = None,
    pipeline_id: int | None = None,
    amo: Any | None = None,
    live: bool = True,
) -> str:
    """Priority: start tag (resume + extinguish) > stop tag (pause).

    Never reads amo ``responsible_user_id``. Pause/resume is tags + C only.
    """
    start, stop = tag_start_name(), tag_stop_name()
    names = [n for n in names if n]
    lid = lead_id if lead_id is not None else getattr(session, "amo_lead_id", None)

    if _should_honor_start(session, names, start):
        resume_bot_manual(
            session, ownership,
            reason="resume via tag",
            lead_id=lid, pipeline_id=pipeline_id,
        )
        cleared = False
        if amo is not None and lid:
            cleared = clear_bot_control_tags(amo, int(lid))
        # One-shot until a *live* GET shows the tag gone (not a stripped cache).
        session.amo_start_tag_present = True
        session.cached_amo_tag_names = (
            [n for n in names if n not in (start, stop)] if cleared else list(names)
        )
        session.amo_tags_checked_at = _now_iso()
        return "start"

    if stop in names:
        if not session.human_handoff_active:
            activate_stop(
                session, ownership,
                reason="tag stop",
                lead_id=lid, pipeline_id=pipeline_id,
            )
        session.cached_amo_tag_names = list(names)
        if live:
            session.amo_start_tag_present = start in names
            if start not in names:
                session.last_start_absent_at = _now_iso()
        session.amo_tags_checked_at = _now_iso()
        return "stop"

    session.cached_amo_tag_names = list(names)
    if live:
        session.amo_start_tag_present = start in names
        if start not in names:
            session.last_start_absent_at = _now_iso()
    session.amo_tags_checked_at = _now_iso()
    return "none"


def sync_from_amo_tags(
    amo: Any,
    session,
    ownership: ConversationOwnershipState | None = None,
    *,
    force: bool = False,
) -> str:
    """Read lead tags (cached) and apply start/stop. Call before any bot action.

    Ignores ``responsible_user_id`` on the lead — that must not resume or pause.
    """
    lead_id = getattr(session, "amo_lead_id", None)
    if amo is None or not lead_id:
        return "no_lead"
    if not force and _cache_fresh(session):
        return apply_amo_tags(
            list(getattr(session, "cached_amo_tag_names", None) or []),
            session, ownership, lead_id=lead_id, amo=None, live=False,
        )
    getter = getattr(amo, "get_lead", None)
    if callable(getter):
        try:
            lead = getter(lead_id, with_tags=True)
        except TypeError:
            lead = getter(lead_id)
    else:
        lead = amo._req("GET", f"/leads/{int(lead_id)}?with=tags")
    if not isinstance(lead, dict):
        return "no_lead"
    names = _tag_names_from_lead(lead)
    if hasattr(amo, "lead_tag_names") and not names:
        try:
            names = list(amo.lead_tag_names(lead_id))
        except Exception:
            pass
    return apply_amo_tags(
        names, session, ownership,
        lead_id=int(lead.get("id") or lead_id),
        pipeline_id=lead.get("pipeline_id"),
        amo=amo,
    )
