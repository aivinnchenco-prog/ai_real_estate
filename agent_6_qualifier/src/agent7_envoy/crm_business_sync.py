"""Agent7 → amoCRM business event sync (no fake raw chat for source-native).

Order of truth:
1. Persist OwnerResult / OwnerRequest locally
2. Attempt amo business notes/tasks/stage
3. CRM failure must not destroy OwnerResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent7_envoy.crm_visibility import (
    CrmRawChatAction,
    channel_display_name,
    log_owner_request_crm_visibility,
    normalize_crm_channel,
    resolve_owner_channel_crm_policy,
)


@dataclass
class CrmSyncResult:
    channel: str
    visibility: str
    raw_chat_action: str
    raw_chat_skipped_expected: bool = False
    business_synced: bool = False
    business_failed: bool = False
    error_code: str = ""
    notes_written: list[str] = field(default_factory=list)
    detail: str = ""


def attempt_raw_chat_mirror(*, channel: Any, amo: Any = None) -> CrmSyncResult:
    """Raw chat mirror gate. Source-native custom chat or expected skip."""
    policy = resolve_owner_channel_crm_policy(channel)
    ch = policy.channel
    if policy.visibility.value == "CUSTOM_CHAT_MIRROR":
        return CrmSyncResult(
            channel=ch,
            visibility=policy.crm_visibility,
            raw_chat_action="CUSTOM_CHAT_MIRROR",
            detail=policy.detail,
        )
    if policy.raw_chat_mirroring:
        return CrmSyncResult(
            channel=ch,
            visibility=policy.crm_visibility,
            raw_chat_action="EXTERNAL_OR_EXISTING",
            detail=policy.detail,
        )
    return CrmSyncResult(
        channel=ch,
        visibility=policy.crm_visibility,
        raw_chat_action=CrmRawChatAction.SKIPPED_EXPECTED.value,
        raw_chat_skipped_expected=True,
        error_code="CRM_RAW_CHAT_UNSUPPORTED_FOR_CHANNEL",
        detail="raw_chat_mirror=SKIPPED_EXPECTED (normal for this channel)",
    )


def outreach_started_note(channel: Any) -> str:
    name = channel_display_name(channel)
    key = normalize_crm_channel(channel)
    if key == "facebook_messenger":
        return "Запрос владельцу отправлен через Facebook Messenger."
    if key == "airbnb_messages":
        return "Запрос владельцу отправлен через Airbnb."
    return f"Запрос владельцу отправлен через {name}."


def owner_reply_business_note(
    *,
    channel: Any,
    verdict_status: str,
    busy_until: Any = None,
    new_price_month: float | None = None,
    conditions_note: str = "",
    include_raw_excerpt: str = "",
) -> str:
    """Human-readable business note. Source-native: no private transcript dump."""
    policy = resolve_owner_channel_crm_policy(channel)
    status = (verdict_status or "").strip().lower()

    if status == "free":
        summary = "Владелец подтвердил доступность."
    elif status == "busy":
        if busy_until:
            summary = f"Владелец сообщил, что объект занят до {busy_until}."
        else:
            summary = "Владелец сообщил, что объект занят."
    elif status in {"conditions_changed", "conditions"}:
        parts = ["Владелец сообщил новые условия"]
        if new_price_month:
            price = f"{new_price_month:,.0f}".replace(",", " ")
            parts.append(f"{price} THB / month")
        if conditions_note:
            parts.append(conditions_note[:160])
        summary = ": ".join([parts[0], "; ".join(parts[1:])]) if len(parts) > 1 else parts[0] + "."
        if not summary.endswith("."):
            summary += "."
    else:
        summary = "Получен ответ владельца (статус уточняется)."

    prefix = f"[{channel_display_name(channel)}] "
    # FULL_CHAT / WA legacy: may append short excerpt; BUSINESS_EVENTS_ONLY: never.
    if policy.raw_chat_mirroring and include_raw_excerpt:
        excerpt = include_raw_excerpt.strip().replace("\n", " ")[:180]
        return prefix + summary + f" Ответ: {excerpt}"
    # TG historical behavior included reply text in notes — keep mild excerpt only
    # when visibility is BUSINESS_EVENTS_ONLY but channel is telegram (existing).
    if normalize_crm_channel(channel) == "telegram" and include_raw_excerpt:
        excerpt = include_raw_excerpt.strip().replace("\n", " ")[:180]
        return prefix + summary + f" Ответ: {excerpt}"
    return prefix + summary


def sync_owner_outreach_started(
    amo: Any,
    session: Any,
    *,
    channel: Any,
    object_id: str = "",
    owner_request_id: str = "",
    dry_run: bool = False,
) -> CrmSyncResult:
    """Business event: OWNER_OUTREACH_STARTED (+ task). Never invents raw chat."""
    policy = resolve_owner_channel_crm_policy(channel)
    mirror = attempt_raw_chat_mirror(channel=channel, amo=amo)
    result = CrmSyncResult(
        channel=policy.channel,
        visibility=policy.crm_visibility,
        raw_chat_action=mirror.raw_chat_action,
        raw_chat_skipped_expected=mirror.raw_chat_skipped_expected,
        error_code=mirror.error_code if mirror.raw_chat_skipped_expected else "",
        detail=mirror.detail,
    )
    log_owner_request_crm_visibility(
        owner_request_id=owner_request_id,
        channel=channel,
        status="OUTREACH_STARTED",
        business_event_sync="PENDING",
    )
    if not policy.business_events:
        log_owner_request_crm_visibility(
            owner_request_id=owner_request_id,
            channel=channel,
            status="OUTREACH_STARTED",
            business_event_sync="OFF",
        )
        return result

    lead_id = getattr(session, "amo_lead_id", None) if session is not None else None
    if amo is None or not lead_id or dry_run:
        result.detail = (result.detail + "; " if result.detail else "") + (
            "dry_run_or_no_amo" if dry_run or amo is None or not lead_id else ""
        )
        log_owner_request_crm_visibility(
            owner_request_id=owner_request_id,
            channel=channel,
            status="OUTREACH_STARTED",
            business_event_sync="SKIPPED_NO_AMO",
        )
        return result

    note = outreach_started_note(channel)
    obj = object_id or getattr(getattr(session, "lead", None), "preferred_object_id", "") or "-"
    try:
        amo.note_owner(lead_id, obj, note)
        result.notes_written.append(note)
        from agent6_qualifier.amo_task_service import AmoTaskService

        AmoTaskService(amo).on_owner_outreach_sent(session)
        result.business_synced = True
        log_owner_request_crm_visibility(
            owner_request_id=owner_request_id,
            channel=channel,
            status="OUTREACH_STARTED",
            business_event_sync="SYNCED",
        )
    except Exception as exc:
        result.business_failed = True
        result.error_code = "CRM_BUSINESS_EVENT_WRITE_FAILED"
        result.detail = repr(exc)
        log_owner_request_crm_visibility(
            owner_request_id=owner_request_id,
            channel=channel,
            status="OUTREACH_STARTED",
            business_event_sync="FAILED",
        )
    return result


def sync_owner_reply_business(
    amo: Any,
    session: Any,
    *,
    channel: Any,
    verdict: Any,
    owner_request_id: str = "",
    raw_owner_text: str = "",
    client_reply_preview: str = "",
    dry_run: bool = False,
) -> CrmSyncResult:
    """Business events after owner reply. OwnerResult must already be persisted."""
    policy = resolve_owner_channel_crm_policy(channel)
    mirror = attempt_raw_chat_mirror(channel=channel, amo=amo)
    result = CrmSyncResult(
        channel=policy.channel,
        visibility=policy.crm_visibility,
        raw_chat_action=mirror.raw_chat_action,
        raw_chat_skipped_expected=mirror.raw_chat_skipped_expected,
        error_code=mirror.error_code if mirror.raw_chat_skipped_expected else "",
        detail=mirror.detail,
    )
    if not policy.business_events:
        return result

    lead_id = getattr(session, "amo_lead_id", None) if session is not None else None
    if amo is None or not lead_id or dry_run:
        log_owner_request_crm_visibility(
            owner_request_id=owner_request_id,
            channel=channel,
            status=getattr(verdict, "status", "") or "",
            business_event_sync="SKIPPED_NO_AMO",
        )
        return result

    status = getattr(verdict, "status", "") or ""
    busy = getattr(verdict, "busy_until", None)
    busy_s = busy.isoformat() if hasattr(busy, "isoformat") else (str(busy) if busy else "")
    note = owner_reply_business_note(
        channel=channel,
        verdict_status=status,
        busy_until=busy_s,
        new_price_month=getattr(verdict, "new_price_month", None),
        conditions_note=getattr(verdict, "conditions_note", "") or "",
        include_raw_excerpt=raw_owner_text if policy.raw_chat_mirroring or normalize_crm_channel(channel) == "telegram" else "",
    )
    obj = (
        getattr(getattr(session, "lead", None), "preferred_object_id", None)
        or getattr(getattr(session, "chosen", None), "object_id", None)
        or "-"
    )
    try:
        stages = amo.ensure_pipeline()
        amo.update_lead_status(lead_id, stages["Согласование условий"])
        amo.note_owner(lead_id, obj, note)
        result.notes_written.append(note)
        if client_reply_preview:
            amo.note_client(
                lead_id, obj, f"Сообщено клиенту: {client_reply_preview[:200]}"
            )
        from agent6_qualifier.amo_task_service import AmoTaskService

        task_svc = AmoTaskService(amo)
        task_svc.on_owner_response(session, status)
        task_svc.reconcile_stage(lead_id, "Согласование условий")
        result.business_synced = True
        log_owner_request_crm_visibility(
            owner_request_id=owner_request_id,
            channel=channel,
            status=status,
            business_event_sync="SYNCED",
        )
    except Exception as exc:
        result.business_failed = True
        result.error_code = "CRM_BUSINESS_EVENT_WRITE_FAILED"
        result.detail = repr(exc)
        log_owner_request_crm_visibility(
            owner_request_id=owner_request_id,
            channel=channel,
            status=status,
            business_event_sync="FAILED",
        )
    return result


def persist_owner_result(
    request_store: Any,
    owner_request_id: str,
    *,
    verdict: Any,
    channel: str = "",
    checked_at: str = "",
) -> Any:
    """Persist structured OwnerResult onto OwnerRequest before CRM sync."""
    if not owner_request_id or request_store is None:
        return None
    req = request_store.get(owner_request_id)
    if req is None:
        return None
    stamp = checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = getattr(verdict, "status", "") or ""
    busy = getattr(verdict, "busy_until", None)
    result_payload = {
        "availability": status,
        "busy_until": busy.isoformat() if hasattr(busy, "isoformat") else (str(busy) if busy else ""),
        "new_price": getattr(verdict, "new_price_month", None),
        "conditions_note": getattr(verdict, "conditions_note", "") or "",
        "channel": channel or req.channel,
        "checked_at": stamp,
    }
    req.last_verdict = status
    req.owner_result = result_payload
    req.crm_visibility = resolve_owner_channel_crm_policy(req.channel).crm_visibility
    return request_store.upsert(req)
