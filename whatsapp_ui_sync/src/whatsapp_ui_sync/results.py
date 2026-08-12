"""Result codes for WhatsApp native-list sync."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SyncCode(str, Enum):
    ALREADY_SYNCED = "ALREADY_SYNCED"
    SYNCED = "SYNCED"
    DRY_RUN_PLAN = "DRY_RUN_PLAN"
    SKIPPED_UNKNOWN = "SKIPPED_UNKNOWN"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    WHATSAPP_UI_AUTH_REQUIRED = "WHATSAPP_UI_AUTH_REQUIRED"
    WHATSAPP_LIST_NOT_FOUND = "WHATSAPP_LIST_NOT_FOUND"
    CONTACT_NOT_FOUND = "CONTACT_NOT_FOUND"
    CONTACT_AMBIGUOUS = "CONTACT_AMBIGUOUS"
    UI_CONTRACT_UNCONFIRMED = "UI_CONTRACT_UNCONFIRMED"
    LINKED_DEVICE_CONFLICT = "LINKED_DEVICE_CONFLICT"
    BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"
    WRITE_BLOCKED = "WRITE_BLOCKED"
    RETRYABLE_UI_TIMEOUT = "RETRYABLE_UI_TIMEOUT"
    FAILED = "FAILED"


RETRYABLE_CODES = frozenset({SyncCode.RETRYABLE_UI_TIMEOUT})

NON_RETRYABLE_CODES = frozenset(
    {
        SyncCode.WHATSAPP_UI_AUTH_REQUIRED,
        SyncCode.WHATSAPP_LIST_NOT_FOUND,
        SyncCode.CONTACT_NOT_FOUND,
        SyncCode.CONTACT_AMBIGUOUS,
        SyncCode.UI_CONTRACT_UNCONFIRMED,
        SyncCode.LINKED_DEVICE_CONFLICT,
        SyncCode.BROWSER_UNAVAILABLE,
        SyncCode.WRITE_BLOCKED,
        SyncCode.SKIPPED_UNKNOWN,
        SyncCode.SKIPPED_DISABLED,
    }
)


@dataclass
class SyncResult:
    code: SyncCode = SyncCode.FAILED
    phone: str = ""
    canonical_role: str = ""
    target_list: str | None = None
    current_lists: list[str] = field(default_factory=list)
    would_add: list[str] = field(default_factory=list)
    would_remove: list[str] = field(default_factory=list)
    message: str = ""
    dry_run: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.code in {
            SyncCode.ALREADY_SYNCED,
            SyncCode.SYNCED,
            SyncCode.DRY_RUN_PLAN,
            SyncCode.SKIPPED_UNKNOWN,
            SyncCode.SKIPPED_DISABLED,
        }

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "phone": self.phone,
            "canonical_role": self.canonical_role,
            "target_list": self.target_list,
            "current_lists": list(self.current_lists),
            "would_add": list(self.would_add),
            "would_remove": list(self.would_remove),
            "message": self.message,
            "dry_run": self.dry_run,
            "details": dict(self.details),
        }
