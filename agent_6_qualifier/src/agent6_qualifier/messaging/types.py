"""Provider-independent messaging types for Agent 6."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ConversationOwner(str, Enum):
    BOT_ACTIVE = "BOT_ACTIVE"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class CanonicalInboundMessage:
    provider: str
    channel: str
    message_id: str
    chat_id: str
    phone: str | None
    direction: str
    text: str | None
    timestamp: datetime | None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    sender_name: str | None = None
    channel_id: str | None = None
    raw_event_type: str = ""
    is_from_bot: bool | None = None
    crm_message_id: str | None = None
    redacted_raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboundTextRequest:
    recipient_chat_id: str
    text: str
    crm_message_id: str | None = None
    channel_id: str | None = None
    chat_type: str = "whatsapp"


@dataclass(frozen=True)
class OutboundDocumentRequest:
    """Wazzup document send via public contentUri (no text in same POST)."""

    recipient_chat_id: str
    content_uri: str
    filename: str = ""
    content_type: str = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    crm_message_id: str | None = None
    channel_id: str | None = None
    chat_type: str = "whatsapp"
    local_path: str = ""


@dataclass(frozen=True)
class DryRunSendResult:
    would_send: bool
    provider: str
    channel_id: str
    recipient: str
    text_length: int
    crm_message_id: str | None
    reason: str = ""
    content_uri: str = ""
    filename: str = ""


class ClientMessagingTransport(ABC):
    """Minimal provider-independent client messaging interface."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def healthcheck(self) -> dict[str, Any]: ...

    @abstractmethod
    def normalize_inbound(self, payload: Any) -> list[CanonicalInboundMessage]: ...

    @abstractmethod
    def send_text(self, request: OutboundTextRequest) -> dict[str, Any]: ...

    def send_document(self, request: OutboundDocumentRequest) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.provider_name} send_document not implemented"
        )

    def send_media(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("send_media not required for current Wazzup stage")
