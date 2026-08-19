"""Business and browser screen states (kept separate)."""

from __future__ import annotations

from enum import Enum


class BusinessState(str, Enum):
    NEW = "NEW"
    INTRO_SENT = "INTRO_SENT"
    WAITING_CONTACT = "WAITING_CONTACT"
    CONTACT_RECEIVED = "CONTACT_RECEIVED"
    ROLE_ASKED = "ROLE_ASKED"
    WAITING_ROLE = "WAITING_ROLE"
    COMPLETE = "COMPLETE"
    DECLINED = "DECLINED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class BrowserScreenState(str, Enum):
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    LISTING = "LISTING"
    MESSAGE_BUTTON_AVAILABLE = "MESSAGE_BUTTON_AVAILABLE"
    MESSENGER_OPEN = "MESSENGER_OPEN"
    MESSAGE_INPUT_READY = "MESSAGE_INPUT_READY"
    WAITING_REPLY = "WAITING_REPLY"
    CHECKPOINT = "CHECKPOINT"
    ACCOUNT_PAUSED = "ACCOUNT_PAUSED"
    UNKNOWN = "UNKNOWN"


TERMINAL_BUSINESS_STATES = frozenset({
    BusinessState.COMPLETE,
    BusinessState.DECLINED,
    BusinessState.MANUAL_REVIEW,
})

ACTIVE_CONVERSATION_STATES = frozenset({
    BusinessState.WAITING_CONTACT,
    BusinessState.WAITING_ROLE,
})

INTRO_SENT_STATES = frozenset({
    BusinessState.INTRO_SENT,
    BusinessState.WAITING_CONTACT,
    BusinessState.CONTACT_RECEIVED,
    BusinessState.ROLE_ASKED,
    BusinessState.WAITING_ROLE,
    BusinessState.COMPLETE,
    BusinessState.DECLINED,
    BusinessState.MANUAL_REVIEW,
})

ROLE_ASKED_STATES = frozenset({
    BusinessState.ROLE_ASKED,
    BusinessState.WAITING_ROLE,
    BusinessState.COMPLETE,
    BusinessState.MANUAL_REVIEW,
})


def next_after_intro_sent() -> BusinessState:
    return BusinessState.WAITING_CONTACT


def next_after_contact_received() -> BusinessState:
    return BusinessState.CONTACT_RECEIVED


def next_after_role_asked() -> BusinessState:
    return BusinessState.WAITING_ROLE
