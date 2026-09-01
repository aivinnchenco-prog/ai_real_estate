"""Conversation ownership / human handoff gates for WhatsApp via Wazzup."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from agent6_qualifier.messaging.types import CanonicalInboundMessage, ConversationOwner


def handoff_ttl_hours() -> float:
    return float(os.getenv("AGENT6_HUMAN_HANDOFF_TTL_HOURS", "48"))


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def human_handoff_expired(last_human_activity_at: str | None) -> bool:
    ref = _parse_iso(last_human_activity_at)
    if ref is None:
        # Active handoff without anchor — stay blocked until TTL data exists.
        return False
    age_hours = (datetime.now(timezone.utc) - ref).total_seconds() / 3600.0
    return age_hours >= handoff_ttl_hours()

OutboundSourceClass = Literal[
    "OWN_BOT_OUTBOUND",
    "HUMAN_OUTBOUND",
    "UNKNOWN_EXTERNAL_OUTBOUND",
    "NOT_OUTBOUND",
]


def classify_outbound_source(
    message: CanonicalInboundMessage,
    *,
    known_bot_outbound_ids: set[str] | None = None,
) -> OutboundSourceClass:
    """Passive classification from webhook metadata (no auto-disable of greetings).

    OWN_BOT_OUTBOUND: Agent6 crmMessageId (agent6-*) / is_from_bot / known ids
    HUMAN_OUTBOUND: explicit is_from_bot=false
    UNKNOWN_EXTERNAL_OUTBOUND: outbound without Agent6 id (possible WA/Wazzup/amo greeting)
    """
    if message.direction != "outbound":
        return "NOT_OUTBOUND"

    crm_id = (message.crm_message_id or "").strip()
    known = known_bot_outbound_ids or set()
    if message.is_from_bot is True or (
        crm_id.startswith("agent6-") or crm_id in known
    ):
        return "OWN_BOT_OUTBOUND"
    if message.is_from_bot is False:
        return "HUMAN_OUTBOUND"
    return "UNKNOWN_EXTERNAL_OUTBOUND"


@dataclass
class ConversationOwnershipState:
    chat_id: str
    owner: ConversationOwner = ConversationOwner.BOT_ACTIVE
    reason: str = ""
    manager_takeover: bool = False
    last_human_message_id: str | None = None
    last_human_activity_at: str | None = None
    known_bot_outbound_ids: set[str] = field(default_factory=set)

    def bot_may_reply(self) -> bool:
        from agent6_qualifier.handoff_control import handoff_manual_only

        blocked = self.manager_takeover or self.owner == ConversationOwner.HUMAN_HANDOFF
        if blocked:
            if handoff_manual_only():
                return False
            return human_handoff_expired(self.last_human_activity_at)
        return self.owner == ConversationOwner.BOT_ACTIVE

    def block_reason(self) -> str | None:
        if self.bot_may_reply():
            return None
        if self.manager_takeover:
            return "manager_takeover flag"
        return f"owner={self.owner.value}"


def apply_inbound_to_ownership(
    state: ConversationOwnershipState,
    message: CanonicalInboundMessage,
) -> ConversationOwnershipState:
    """Update ownership conservatively.

    - Known Agent 6 outbound (crmMessageId agent6-* / is_from_bot True): keep BOT_ACTIVE
    - Outbound from other sources (human / phone / amoCRM via Wazzup): HUMAN_HANDOFF
    - If source unknown: do not guess; leave owner unchanged unless manager_takeover
    """
    kind = classify_outbound_source(
        message, known_bot_outbound_ids=state.known_bot_outbound_ids
    )
    if kind == "NOT_OUTBOUND":
        return state

    crm_id = (message.crm_message_id or "").strip()
    if kind == "OWN_BOT_OUTBOUND":
        state.known_bot_outbound_ids.add(crm_id or message.message_id)
        return state

    if kind in ("HUMAN_OUTBOUND", "UNKNOWN_EXTERNAL_OUTBOUND"):
        # C: any outbound not marked agent6-* is a human intervention.
        reason = (
            "outbound not from Agent 6 (explicit is_from_bot=false)"
            if kind == "HUMAN_OUTBOUND"
            else "UNKNOWN_EXTERNAL_OUTBOUND (not agent6-*)"
        )
        from agent6_qualifier.handoff_control import apply_external_outbound_stop

        apply_external_outbound_stop(state, reason=reason)
        state.last_human_message_id = message.message_id
        ts = (
            message.timestamp.isoformat(timespec="seconds")
            if message.timestamp is not None
            else datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
        state.last_human_activity_at = ts
        return state

    return state


def set_manager_takeover(state: ConversationOwnershipState, *, enabled: bool = True) -> ConversationOwnershipState:
    state.manager_takeover = enabled
    if enabled:
        state.owner = ConversationOwner.HUMAN_HANDOFF
        state.reason = "explicit manager takeover"
        state.last_human_activity_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return state


def resume_bot(state: ConversationOwnershipState) -> ConversationOwnershipState:
    """Explicit resume: HUMAN_HANDOFF/PAUSED → BOT_ACTIVE (no timer)."""
    state.manager_takeover = False
    state.owner = ConversationOwner.BOT_ACTIVE
    state.reason = "explicit resume to BOT_ACTIVE"
    return state


def assert_bot_may_send(state: ConversationOwnershipState) -> None:
    reason = state.block_reason()
    if reason:
        raise PermissionError(f"bot send blocked: {reason}")
