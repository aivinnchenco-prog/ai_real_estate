"""Canonical CRM visibility policy for Agent7 owner channels.

Separates OWNER COMMUNICATION from CRM VISIBILITY.

Actual matrix:
- WhatsApp: FULL_CHAT_EXTERNAL_SYNC (Wazzup↔amo)
- Telegram: BUSINESS_EVENTS_ONLY (no TG chat connector)
- Facebook / Airbnb: CUSTOM_CHAT_MIRROR when amo custom channel configured,
  else BUSINESS_EVENTS_ONLY fallback (Agent7 never blocked)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent6_qualifier.models import OwnerChannel


class CrmVisibilityMode(str, Enum):
    FULL_CHAT = "FULL_CHAT"  # legacy alias
    FULL_CHAT_EXTERNAL_SYNC = "FULL_CHAT_EXTERNAL_SYNC"
    CUSTOM_CHAT_MIRROR = "CUSTOM_CHAT_MIRROR"
    BUSINESS_EVENTS_ONLY = "BUSINESS_EVENTS_ONLY"
    NONE = "NONE"


class CrmRawChatAction(str, Enum):
    MIRROR = "MIRROR"
    CUSTOM_MIRROR = "CUSTOM_MIRROR"
    SKIPPED_EXPECTED = "SKIPPED_EXPECTED"
    DEGRADED = "AMO_CHAT_MIRROR_DEGRADED"
    UNSUPPORTED = "CRM_RAW_CHAT_UNSUPPORTED_FOR_CHANNEL"


@dataclass(frozen=True)
class OwnerChannelCrmPolicy:
    channel: str
    visibility: CrmVisibilityMode
    raw_chat_mirroring: bool
    business_events: bool
    business_result_sync: bool
    raw_chat_action: CrmRawChatAction
    detail: str = ""
    custom_chat_configured: bool = False

    @property
    def crm_visibility(self) -> str:
        return self.visibility.value


def _amo_custom_ready(channel_key: str) -> bool:
    try:
        from agent7_envoy.amo_chat.config import load_amo_chat_config

        cfg = load_amo_chat_config()
        ch = cfg.channel(channel_key)
        return bool(ch.configured and ch.connected)
    except Exception:
        return False


def normalize_crm_channel(channel: Any) -> str:
    if channel is None:
        return ""
    if isinstance(channel, OwnerChannel):
        if channel in (OwnerChannel.FACEBOOK_MESSENGER, OwnerChannel.FB_MARKETPLACE):
            return "facebook_messenger"
        if channel in (OwnerChannel.AIRBNB_MESSAGES, OwnerChannel.AIRBNB):
            return "airbnb_messages"
        return channel.value
    text = str(channel).strip().lower()
    aliases = {
        "wa": "whatsapp",
        "whatsapp": "whatsapp",
        "tg": "telegram",
        "telegram": "telegram",
        "facebook": "facebook_messenger",
        "facebook_messenger": "facebook_messenger",
        "fb_marketplace": "facebook_messenger",
        "fb": "facebook_messenger",
        "airbnb": "airbnb_messages",
        "airbnb_messages": "airbnb_messages",
    }
    return aliases.get(text, text)


def resolve_owner_channel_crm_policy(channel: Any) -> OwnerChannelCrmPolicy:
    key = normalize_crm_channel(channel)
    if key == "whatsapp":
        return OwnerChannelCrmPolicy(
            channel=key,
            visibility=CrmVisibilityMode.FULL_CHAT_EXTERNAL_SYNC,
            raw_chat_mirroring=True,
            business_events=True,
            business_result_sync=True,
            raw_chat_action=CrmRawChatAction.MIRROR,
            detail="Wazzup↔amoCRM external chat sync + Agent7 notes/tasks",
        )
    if key == "telegram":
        return OwnerChannelCrmPolicy(
            channel=key,
            visibility=CrmVisibilityMode.BUSINESS_EVENTS_ONLY,
            raw_chat_mirroring=False,
            business_events=True,
            business_result_sync=True,
            raw_chat_action=CrmRawChatAction.SKIPPED_EXPECTED,
            detail="No TG→amo raw chat connector; notes/tasks only",
        )
    if key == "facebook_messenger":
        ready = _amo_custom_ready("facebook")
        if ready:
            return OwnerChannelCrmPolicy(
                channel=key,
                visibility=CrmVisibilityMode.CUSTOM_CHAT_MIRROR,
                raw_chat_mirroring=True,
                business_events=True,
                business_result_sync=True,
                raw_chat_action=CrmRawChatAction.CUSTOM_MIRROR,
                detail="Open Home | Facebook Marketplace custom chat",
                custom_chat_configured=True,
            )
        return OwnerChannelCrmPolicy(
            channel=key,
            visibility=CrmVisibilityMode.BUSINESS_EVENTS_ONLY,
            raw_chat_mirroring=False,
            business_events=True,
            business_result_sync=True,
            raw_chat_action=CrmRawChatAction.SKIPPED_EXPECTED,
            detail="FB custom chat not configured → business events only",
            custom_chat_configured=False,
        )
    if key == "airbnb_messages":
        ready = _amo_custom_ready("airbnb")
        if ready:
            return OwnerChannelCrmPolicy(
                channel=key,
                visibility=CrmVisibilityMode.CUSTOM_CHAT_MIRROR,
                raw_chat_mirroring=True,
                business_events=True,
                business_result_sync=True,
                raw_chat_action=CrmRawChatAction.CUSTOM_MIRROR,
                detail="Open Home | Airbnb custom chat",
                custom_chat_configured=True,
            )
        return OwnerChannelCrmPolicy(
            channel=key,
            visibility=CrmVisibilityMode.BUSINESS_EVENTS_ONLY,
            raw_chat_mirroring=False,
            business_events=True,
            business_result_sync=True,
            raw_chat_action=CrmRawChatAction.SKIPPED_EXPECTED,
            detail="Airbnb custom chat not configured → business events only",
            custom_chat_configured=False,
        )
    return OwnerChannelCrmPolicy(
        channel=key or "unknown",
        visibility=CrmVisibilityMode.NONE,
        raw_chat_mirroring=False,
        business_events=False,
        business_result_sync=False,
        raw_chat_action=CrmRawChatAction.UNSUPPORTED,
        detail="unknown channel — no CRM visibility",
    )


class OwnerChannelCrmPolicyResolver:
    def resolve(self, channel: Any) -> OwnerChannelCrmPolicy:
        return resolve_owner_channel_crm_policy(channel)


def should_mirror_raw_chat(channel: Any) -> bool:
    return resolve_owner_channel_crm_policy(channel).raw_chat_mirroring


def should_sync_business_events(channel: Any) -> bool:
    return resolve_owner_channel_crm_policy(channel).business_events


def channel_display_name(channel: Any) -> str:
    key = normalize_crm_channel(channel)
    return {
        "whatsapp": "WhatsApp",
        "telegram": "Telegram",
        "facebook_messenger": "Facebook Messenger",
        "airbnb_messages": "Airbnb",
    }.get(key, key or "unknown")


def log_owner_request_crm_visibility(
    *,
    owner_request_id: str,
    channel: Any,
    status: str = "",
    business_event_sync: str = "",
) -> list[str]:
    policy = resolve_owner_channel_crm_policy(channel)
    if policy.raw_chat_action == CrmRawChatAction.CUSTOM_MIRROR:
        raw = "CUSTOM_CHAT_MIRROR"
    elif policy.raw_chat_mirroring:
        raw = "ON"
    elif policy.raw_chat_action == CrmRawChatAction.SKIPPED_EXPECTED:
        raw = "SKIPPED_EXPECTED"
    else:
        raw = policy.raw_chat_action.value
    lines = [
        f"OWNER_REQUEST: {owner_request_id or '-'}",
        f"CHANNEL: {normalize_crm_channel(channel).upper() or '-'}",
        f"CRM_VISIBILITY: {policy.crm_visibility}",
        f"RAW_CHAT: {raw}",
        f"BUSINESS_EVENT: {business_event_sync or ('ENABLED' if policy.business_events else 'OFF')}",
    ]
    if status:
        lines.append(f"STATUS: {status}")
    for line in lines:
        print(f"[agent7.crm] {line}")
    return lines
