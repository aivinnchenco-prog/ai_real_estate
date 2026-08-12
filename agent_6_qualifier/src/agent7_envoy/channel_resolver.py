"""Canonical owner channel resolution for Agent7.

Priority: WHATSAPP → TELEGRAM → SOURCE_NATIVE (FB Messenger / Airbnb Messages) → NONE.

Do not scatter source-native if/else across Agent7 handlers — call resolve_owner_channel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agent6_qualifier.models import LeadProfile, Listing, OwnerChannel
from agent6_qualifier.rental_policy import evaluate_rental_policy, listing_source_kind


class OwnerMessagingChannel(str, Enum):
    """Transport-facing channel names (OwnerChannelDecision.channel)."""

    WHATSAPP = "WHATSAPP"
    TELEGRAM = "TELEGRAM"
    FACEBOOK_MESSENGER = "FACEBOOK_MESSENGER"
    AIRBNB_MESSAGES = "AIRBNB_MESSAGES"
    NONE = "NONE"


@dataclass(frozen=True)
class OwnerChannelDecision:
    channel: OwnerMessagingChannel
    reason: str
    listing_source: str  # facebook | airbnb | unknown | …
    destination: str = ""
    owner_channel: OwnerChannel | None = None  # template/outreach enum
    policy_blocked: bool = False
    policy_reason: str = ""


_TG_RE = re.compile(r"^@?[A-Za-z][A-Za-z0-9_]{2,}$")
_INVALID_MARKERS = frozenset(
    {"", "n/a", "na", "none", "-", "нет", "null", "undefined", "unknown"}
)


def _whatsapp_valid(raw: str) -> bool:
    from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits

    text = (raw or "").strip()
    if text.lower() in _INVALID_MARKERS:
        return False
    digits = normalize_phone_e164_digits(text)
    if not digits:
        return False
    # Soft floor so fixtures like +66123 still resolve; reject tiny garbage.
    if len(digits) < 5 or len(digits) > 15:
        return False
    return True


def _telegram_valid(raw: str) -> bool:
    text = (raw or "").strip()
    if not text or text.lower() in _INVALID_MARKERS:
        return False
    if text.startswith("https://t.me/") or text.startswith("http://t.me/"):
        handle = text.rstrip("/").rsplit("/", 1)[-1]
        return bool(_TG_RE.match(handle))
    return bool(_TG_RE.match(text))


def _is_facebook_source(listing: Listing) -> bool:
    return listing_source_kind(listing) == "facebook"


def _is_airbnb_source(listing: Listing) -> bool:
    return listing_source_kind(listing) == "airbnb"


def resolve_owner_channel(
    listing: Listing,
    lead: LeadProfile | None = None,
) -> OwnerChannelDecision:
    """Resolve owner messaging channel with WA → TG → source-native priority."""
    source = listing_source_kind(listing)
    wa = (listing.owner_whatsapp or "").strip()
    tg = (listing.owner_telegram or "").strip()
    src_url = (listing.source_url or "").strip()

    if wa and _whatsapp_valid(wa):
        return OwnerChannelDecision(
            channel=OwnerMessagingChannel.WHATSAPP,
            reason="valid_owner_whatsapp",
            listing_source=source,
            destination=wa,
            owner_channel=OwnerChannel.WHATSAPP,
        )

    if tg and _telegram_valid(tg):
        dest = tg
        if not tg.startswith("@") and not tg.startswith("http"):
            dest = f"@{tg}"
        return OwnerChannelDecision(
            channel=OwnerMessagingChannel.TELEGRAM,
            reason="valid_owner_telegram"
            + ("; whatsapp_invalid" if wa else ""),
            listing_source=source,
            destination=dest,
            owner_channel=OwnerChannel.TELEGRAM,
        )

    # SOURCE_NATIVE only when direct contacts empty/invalid
    if _is_facebook_source(listing) and src_url:
        if lead is not None:
            pol = evaluate_rental_policy(listing, lead)
            if not pol.ok:
                return OwnerChannelDecision(
                    channel=OwnerMessagingChannel.NONE,
                    reason="OWNER_CONTACT_UNAVAILABLE: facebook_policy_short_stay",
                    listing_source=source,
                    destination="",
                    owner_channel=None,
                    policy_blocked=True,
                    policy_reason=pol.reason or "facebook_long_term_only",
                )
        return OwnerChannelDecision(
            channel=OwnerMessagingChannel.FACEBOOK_MESSENGER,
            reason="source_native_facebook_marketplace",
            listing_source=source,
            destination=src_url,
            owner_channel=OwnerChannel.FACEBOOK_MESSENGER,
        )

    if _is_airbnb_source(listing) and src_url:
        return OwnerChannelDecision(
            channel=OwnerMessagingChannel.AIRBNB_MESSAGES,
            reason="source_native_airbnb",
            listing_source=source,
            destination=src_url,
            owner_channel=OwnerChannel.AIRBNB_MESSAGES,
        )

    detail = "no_valid_direct_contact"
    if wa and not _whatsapp_valid(wa):
        detail = "whatsapp_invalid_and_no_fallback"
    if not src_url:
        detail = "no_contact_and_no_source_url"
    elif source == "unknown":
        detail = "unknown_source_no_native_channel"

    return OwnerChannelDecision(
        channel=OwnerMessagingChannel.NONE,
        reason=f"OWNER_CONTACT_UNAVAILABLE: {detail}",
        listing_source=source,
        destination="",
        owner_channel=None,
    )


class OwnerChannelResolver:
    """Thin callable wrapper — single entry point for Agent7 handlers."""

    def resolve(
        self,
        listing: Listing,
        lead: LeadProfile | None = None,
    ) -> OwnerChannelDecision:
        return resolve_owner_channel(listing, lead)


def decision_to_owner_channel_tuple(
    decision: OwnerChannelDecision,
) -> Optional[tuple[OwnerChannel, str]]:
    """Backward-compatible (OwnerChannel, contact) for Listing.owner_channel()."""
    if decision.owner_channel is None or decision.channel == OwnerMessagingChannel.NONE:
        return None
    return decision.owner_channel, decision.destination
