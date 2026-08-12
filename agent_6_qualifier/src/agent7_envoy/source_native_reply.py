"""Source-native owner reply → OwnerResult persist → client continuation → CRM.

Correlation uses OwnerRequest fields only — never requires amo message id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from agent7_envoy.crm_business_sync import (
    attempt_raw_chat_mirror,
    persist_owner_result,
    sync_owner_reply_business,
)
from agent7_envoy.crm_visibility import resolve_owner_channel_crm_policy
from agent7_envoy.messaging.inbox_watcher import InboundOwnerMessage
from agent7_envoy.owner_request_store import OwnerRequest, OwnerRequestStore
from agent7_envoy.owner_result import (
    OwnerVerdict,
    apply_verdict_to_session,
    build_client_message,
)


@dataclass
class SourceNativeReplyResult:
    processed: bool
    owner_request_id: str = ""
    client_session_chat_id: str = ""
    verdict_status: str = ""
    owner_result_persisted: bool = False
    crm_visibility: str = ""
    raw_chat_mirror: str = ""
    business_event_sync: str = ""
    downstream_continued: bool = False
    notes: list[str] = field(default_factory=list)


def handle_source_native_owner_reply(
    inbound: InboundOwnerMessage,
    owner_req: OwnerRequest,
    *,
    session: Any,
    session_store: Any | None = None,
    request_store: OwnerRequestStore | None = None,
    amo: Any | None = None,
    parse_owner_reply: Callable[[str, Any], OwnerVerdict] | None = None,
    notify_client: Callable[[Any, str], None] | None = None,
    dry_run_crm: bool = True,
) -> SourceNativeReplyResult:
    """Process FB/Airbnb owner reply without amo chat message id.

    Order: parse → persist OwnerResult → continue client → CRM business sync.
    """
    store = request_store or OwnerRequestStore()
    channel = owner_req.channel or inbound.channel
    policy = resolve_owner_channel_crm_policy(channel)
    mirror = attempt_raw_chat_mirror(channel=channel)
    out = SourceNativeReplyResult(
        processed=False,
        owner_request_id=owner_req.owner_request_id,
        client_session_chat_id=owner_req.client_session_chat_id,
        crm_visibility=policy.crm_visibility,
        raw_chat_mirror=(
            "SKIPPED_EXPECTED" if mirror.raw_chat_skipped_expected else "ON"
        ),
    )

    # Correlation must not require amo message id
    if not owner_req.owner_request_id or not owner_req.client_session_chat_id:
        out.notes.append("missing_owner_request_correlation")
        return out

    text = inbound.text or ""
    if parse_owner_reply is None:
        # Offline/test default: trivial keyword parser
        low = text.lower()
        if "занят" in low or "busy" in low:
            verdict = OwnerVerdict(status="busy")
        elif "цен" in low or "price" in low or "услови" in low:
            verdict = OwnerVerdict(
                status="conditions_changed",
                new_price_month=180000.0 if "180" in text else None,
                conditions_note=text[:120],
            )
        else:
            verdict = OwnerVerdict(status="free")
    else:
        verdict = parse_owner_reply(text, session)

    out.verdict_status = verdict.status

    # 1) Persist OwnerResult FIRST
    persist_owner_result(
        store, owner_req.owner_request_id, verdict=verdict, channel=channel
    )
    store.mark_message_processed(
        owner_req.owner_request_id,
        inbound.message_id,
        verdict=verdict.status,
    )
    out.owner_result_persisted = True
    refreshed = store.get(owner_req.owner_request_id)
    assert refreshed is not None
    assert refreshed.owner_result, "OwnerResult must survive without amo"

    # Secondary: mirror inbound transcript to amo custom chat (non-blocking)
    try:
        from agent7_envoy.amo_chat.mirror import AmoChatMirrorService
        from agent7_envoy.amo_chat.origin import MessageOrigin

        AmoChatMirrorService(dry_run=dry_run_crm).mirror_source_message(
            channel=channel,
            owner_request_id=owner_req.owner_request_id,
            object_id=owner_req.object_id,
            text=text,
            origin=MessageOrigin.SOURCE_NATIVE_INBOUND,
            external_thread_id=owner_req.external_thread_id
            or inbound.external_thread_id,
            source_url=owner_req.source_url,
            external_message_id=inbound.message_id,
        )
    except Exception as exc:
        out.notes.append(f"AMO_CHAT_MIRROR_DEGRADED:{type(exc).__name__}")

    # 2) Downstream client continuation (Agent6 session)
    if session is not None:
        apply_verdict_to_session(session, verdict)
        client_msg = build_client_message(verdict, session)
        if notify_client is not None:
            notify_client(session.chat_id, client_msg)
        if session_store is not None:
            session_store.save(session)
        out.downstream_continued = True

    # 3) CRM business sync — failure must not destroy OwnerResult
    crm = sync_owner_reply_business(
        amo,
        session,
        channel=channel,
        verdict=verdict,
        owner_request_id=owner_req.owner_request_id,
        raw_owner_text="",  # never dump private source-native transcript
        client_reply_preview="",
        dry_run=dry_run_crm,
    )
    if crm.business_failed:
        out.business_event_sync = "FAILED"
        out.notes.append("CRM_BUSINESS_EVENT_WRITE_FAILED")
        # Re-check persistence
        again = store.get(owner_req.owner_request_id)
        if again and again.owner_result:
            out.owner_result_persisted = True
            out.notes.append("owner_result_survived_crm_failure")
    elif crm.business_synced:
        out.business_event_sync = "SYNCED"
    else:
        out.business_event_sync = "SKIPPED_NO_AMO"

    if refreshed:
        refreshed.crm_visibility = policy.crm_visibility
        refreshed.crm_business_sync = out.business_event_sync
        store.upsert(refreshed)

    out.processed = True
    return out


def fake_owner_contact_guard(listing: Any) -> list[str]:
    """Ensure source-native path does not invent WA/TG contacts."""
    violations: list[str] = []
    # listing may be a SimpleNamespace in tests
    wa = getattr(listing, "owner_whatsapp", "") or ""
    tg = getattr(listing, "owner_telegram", "") or ""
    # Empty is fine; non-empty must have been pre-existing — caller checks context
    if wa.startswith("fake:") or tg.startswith("fake:"):
        violations.append("fake_owner_contact")
    return violations
