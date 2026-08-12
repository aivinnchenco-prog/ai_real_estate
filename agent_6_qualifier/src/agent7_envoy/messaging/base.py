"""Shared OwnerMessagingTransport contract for Agent7 outbound channels."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def agent7_live_enabled() -> bool:
    return _env_bool("AGENT7_LIVE_OUTREACH_ENABLED", False)


def facebook_messenger_enabled() -> bool:
    return _env_bool("AGENT7_FACEBOOK_MESSENGER_ENABLED", False)


def airbnb_messages_enabled() -> bool:
    return _env_bool("AGENT7_AIRBNB_MESSAGES_ENABLED", False)


def repo_runtime_root() -> Path:
    # agent7_envoy/messaging/base.py → parents[4] = refactor root
    return Path(__file__).resolve().parents[4]


def browser_profiles_root() -> Path:
    override = (os.getenv("AGENT7_BROWSER_PROFILES_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return repo_runtime_root() / "runtime" / "browser_profiles"


def browser_failures_root() -> Path:
    override = (os.getenv("AGENT7_BROWSER_FAILURES_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return repo_runtime_root() / "runtime" / "agent7_browser_failures"


class AuthStatus(str, Enum):
    READY = "READY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    BLOCKED = "BLOCKED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class SendOutcome(str, Enum):
    SENT = "SENT"
    DRY_RUN_READY = "READY_TO_SEND"
    BLOCKED = "BLOCKED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    DUPLICATE = "DUPLICATE"
    UNKNOWN_SEND_STATE = "UNKNOWN_SEND_STATE"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    COMPOSER_NOT_FOUND = "COMPOSER_NOT_FOUND"
    LISTING_NOT_OPENED = "LISTING_NOT_OPENED"
    MESSAGE_ACTION_NOT_FOUND = "MESSAGE_ACTION_NOT_FOUND"
    THREAD_NOT_IDENTIFIED = "THREAD_NOT_IDENTIFIED"
    LIVE_OFF = "LIVE_OFF"
    CHANNEL_DISABLED = "CHANNEL_DISABLED"


@dataclass
class TransportStageDiagnostics:
    listing_opened: bool = False
    message_action_found: bool = False
    composer_found: bool = False
    thread_identified: bool = False
    seller_context_confirmed: bool = False
    stages: list[str] = field(default_factory=list)
    failure_stage: str = ""
    notes: list[str] = field(default_factory=list)

    def mark(self, stage: str, ok: bool = True) -> None:
        label = stage if ok else f"{stage}_FAILED"
        self.stages.append(label)
        if not ok and not self.failure_stage:
            self.failure_stage = stage


@dataclass
class HealthcheckResult:
    channel: str
    status: AuthStatus
    live_global: bool
    channel_enabled: bool
    ready: bool
    detail: str = ""
    profile_dir: str = ""


@dataclass
class SendTextRequest:
    text: str
    destination: str  # phone / @user / listing URL
    owner_request_id: str = ""
    object_id: str = ""
    source_url: str = ""
    client_session_chat_id: str = ""
    dry_run: bool = True
    expected_thread_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SendTextResult:
    outcome: SendOutcome
    channel: str
    dry_run: bool = True
    external_thread_id: str = ""
    external_conversation_url: str = ""
    external_message_id: str = ""
    blocker: str = ""
    diagnostics: TransportStageDiagnostics = field(
        default_factory=TransportStageDiagnostics
    )
    screenshot_path: str = ""
    prepared_text: str = ""


@runtime_checkable
class OwnerMessagingTransport(Protocol):
    channel_name: str

    def is_ready(self) -> bool: ...

    def healthcheck(self) -> HealthcheckResult: ...

    def send_text(self, request: SendTextRequest) -> SendTextResult: ...
