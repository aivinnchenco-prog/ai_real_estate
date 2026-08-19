"""Dispatch Agent7 OutreachPlan via the correct OwnerMessagingTransport."""

from __future__ import annotations

from dataclasses import dataclass

from agent6_qualifier.models import OwnerChannel
from agent7_envoy.messaging.airbnb_messages import AirbnbMessagesOwnerTransport
from agent7_envoy.messaging.base import (
    SendOutcome,
    SendTextRequest,
    SendTextResult,
    agent7_live_enabled,
    airbnb_messages_enabled,
    facebook_messenger_enabled,
)
from agent7_envoy.messaging.facebook_messenger import FacebookMessengerOwnerTransport
from agent7_envoy.messaging.telegram import TelegramOwnerTransport
from agent7_envoy.messaging.whatsapp import WhatsAppOwnerTransport
from agent7_envoy.owner_request_store import OwnerRequest, OwnerRequestStore
from agent7_envoy.outreach import OutreachPlan


@dataclass
class DispatchResult:
    result: SendTextResult
    owner_request: OwnerRequest | None = None
    duplicate_blocked: bool = False


def _channel_key(channel: OwnerChannel | None) -> str:
    if channel is None:
        return ""
    if channel in (OwnerChannel.FACEBOOK_MESSENGER, OwnerChannel.FB_MARKETPLACE):
        return "facebook_messenger"
    if channel in (OwnerChannel.AIRBNB_MESSAGES, OwnerChannel.AIRBNB):
        return "airbnb_messages"
    return channel.value


def dispatch_outreach_plan(
    plan: OutreachPlan,
    *,
    client_session_chat_id: str,
    request_store: OwnerRequestStore | None = None,
    dry_run: bool = True,
    facebook_transport: FacebookMessengerOwnerTransport | None = None,
    airbnb_transport: AirbnbMessagesOwnerTransport | None = None,
    whatsapp_transport: WhatsAppOwnerTransport | None = None,
    telegram_transport: TelegramOwnerTransport | None = None,
) -> DispatchResult:
    """Send OutreachPlan text through the matching transport + OwnerRequest correlation."""
    store = request_store or OwnerRequestStore()
    channel = plan.channel
    ch_key = _channel_key(channel)
    text = plan.first_message or ""
    listing = plan.listing

    if channel is None:
        return DispatchResult(
            result=SendTextResult(
                outcome=SendOutcome.BLOCKED,
                channel="none",
                dry_run=dry_run,
                blocker="no_channel",
            )
        )

    source = {
        "whatsapp": "WHATSAPP",
        "telegram": "TELEGRAM",
        "facebook_messenger": "FACEBOOK",
        "airbnb_messages": "AIRBNB",
    }.get(ch_key, ch_key.upper())

    existing = store.list_open_for_client_object(
        client_session_chat_id=client_session_chat_id,
        object_id=listing.object_id,
        channel=ch_key,
    )
    if existing:
        owner_req = existing[0]
        if store.already_sent(owner_req.owner_request_id, ch_key):
            return DispatchResult(
                result=SendTextResult(
                    outcome=SendOutcome.DUPLICATE,
                    channel=ch_key,
                    dry_run=dry_run,
                    blocker="duplicate_outbound",
                    prepared_text=text,
                ),
                owner_request=owner_req,
                duplicate_blocked=True,
            )
        if owner_req.status == "UNKNOWN_SEND_STATE":
            return DispatchResult(
                result=SendTextResult(
                    outcome=SendOutcome.UNKNOWN_SEND_STATE,
                    channel=ch_key,
                    dry_run=dry_run,
                    blocker="UNKNOWN_SEND_STATE_requires_reconciliation",
                    prepared_text=text,
                ),
                owner_request=owner_req,
                duplicate_blocked=True,
            )
    else:
        owner_req = store.create(
            owner_phone=plan.contact if ch_key == "whatsapp" else "",
            object_id=listing.object_id,
            client_session_chat_id=client_session_chat_id,
            channel=ch_key,
            source=source,
            source_url=listing.source_url,
            outbound_message=text,
        )

    req = SendTextRequest(
        text=text,
        destination=plan.contact or listing.source_url,
        owner_request_id=owner_req.owner_request_id,
        object_id=listing.object_id,
        source_url=listing.source_url,
        client_session_chat_id=client_session_chat_id,
        dry_run=dry_run,
        expected_thread_id=owner_req.external_thread_id,
    )

    if ch_key == "whatsapp":
        transport = whatsapp_transport or WhatsAppOwnerTransport()
    elif ch_key == "telegram":
        transport = telegram_transport or TelegramOwnerTransport()
    elif ch_key == "facebook_messenger":
        transport = facebook_transport or FacebookMessengerOwnerTransport()
    elif ch_key == "airbnb_messages":
        transport = airbnb_transport or AirbnbMessagesOwnerTransport()
    else:
        return DispatchResult(
            result=SendTextResult(
                outcome=SendOutcome.BLOCKED,
                channel=ch_key,
                dry_run=dry_run,
                blocker="unsupported_channel",
            ),
            owner_request=owner_req,
        )

    result = transport.send_text(req)

    if result.outcome == SendOutcome.SENT:
        store.mark_sent(
            owner_req.owner_request_id,
            external_thread_id=result.external_thread_id,
            external_conversation_url=result.external_conversation_url,
            external_message_id=result.external_message_id,
            outbound_message=text,
            send_outcome="sent",
        )
        store.mark_awaiting(owner_req.owner_request_id)
        # Secondary: amo custom chat mirror (never blocks business flow)
        try:
            import os

            from agent7_envoy.amo_chat.mirror import AmoChatMirrorService
            from agent7_envoy.amo_chat.origin import MessageOrigin

            mirror_live = (os.getenv("AMO_CHAT_MIRROR_LIVE") or "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            AmoChatMirrorService(dry_run=not mirror_live).mirror_source_message(
                channel=ch_key,
                owner_request_id=owner_req.owner_request_id,
                object_id=listing.object_id,
                text=text,
                origin=MessageOrigin.SOURCE_NATIVE_OUTBOUND,
                external_thread_id=result.external_thread_id,
                source_url=listing.source_url,
                external_message_id=result.external_message_id,
            )
        except Exception as exc:
            print(f"[agent7.amo_chat] AMO_CHAT_MIRROR_DEGRADED outbound: {type(exc).__name__}")
    elif result.outcome == SendOutcome.DRY_RUN_READY:
        # Persist correlation for dry-run readiness without marking as live-sent
        owner_req.external_thread_id = result.external_thread_id
        owner_req.external_conversation_url = result.external_conversation_url
        owner_req.send_outcome = "dry_run"
        store.upsert(owner_req)
    elif result.outcome == SendOutcome.UNKNOWN_SEND_STATE:
        store.mark_unknown_send(owner_req.owner_request_id)
    elif result.outcome == SendOutcome.DUPLICATE:
        pass
    else:
        owner_req.send_outcome = result.outcome.value.lower()
        store.upsert(owner_req)

    return DispatchResult(result=result, owner_request=store.get(owner_req.owner_request_id))


def source_native_live_allowed(channel: OwnerChannel | None) -> bool:
    if not agent7_live_enabled():
        return False
    ch = _channel_key(channel)
    if ch == "facebook_messenger":
        return facebook_messenger_enabled()
    if ch == "airbnb_messages":
        return airbnb_messages_enabled()
    return True
