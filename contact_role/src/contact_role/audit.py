"""Audit events for canonical role changes (no sensitive payloads)."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .phone import mask_phone
from .roles import CanonicalRole
from .sources import RoleSource


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_audit_path() -> Path:
    override = (os.getenv("CONTACT_ROLE_AUDIT_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "contact_role_audit.jsonl"


@dataclass
class AuditEvent:
    event: str
    contact_key: str
    contact_id: int | None
    masked_phone: str
    old_role: str
    new_role: str
    role_source: str
    previous_source: str
    trigger: str
    timestamp: str
    candidate_role: str = ""
    accepted_role: str = ""
    priority: str = ""
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data.get("extra"):
            data.pop("extra", None)
        return data


# Backward-compatible alias
ContactRoleChangedEvent = AuditEvent


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.Lock()
        self.events: list[AuditEvent] = []

    def _write(self, event: AuditEvent) -> AuditEvent:
        self.events.append(event)
        if self.path is not None:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return event

    def emit_role_changed(
        self,
        *,
        contact_key: str,
        phone: str | None,
        old_role: CanonicalRole | str,
        new_role: CanonicalRole | str,
        role_source: RoleSource | str,
        previous_source: RoleSource | str,
        trigger: str,
        contact_id: int | None = None,
    ) -> AuditEvent:
        return self._write(
            AuditEvent(
                event="CONTACT_ROLE_CHANGED",
                contact_key=contact_key,
                contact_id=contact_id,
                masked_phone=mask_phone(phone),
                old_role=CanonicalRole.parse(old_role).value,
                new_role=CanonicalRole.parse(new_role).value,
                role_source=RoleSource.parse(role_source).value,
                previous_source=RoleSource.parse(previous_source).value,
                trigger=trigger,
                timestamp=_now(),
                accepted_role=CanonicalRole.parse(new_role).value,
            )
        )

    def emit_assignment_rejected(
        self,
        *,
        contact_key: str,
        phone: str | None,
        old_role: CanonicalRole | str,
        candidate_role: CanonicalRole | str,
        role_source: RoleSource | str,
        previous_source: RoleSource | str,
        trigger: str,
        reason: str,
        contact_id: int | None = None,
        priority: str = "",
    ) -> AuditEvent:
        """CONTACT_ROLE_ASSIGNMENT_REJECTED / ROLE_ASSIGNMENT_REJECTED_BY_PRIORITY."""
        event_name = (
            "ROLE_ASSIGNMENT_REJECTED_BY_PRIORITY"
            if reason == "priority_blocked"
            else "CONTACT_ROLE_ASSIGNMENT_REJECTED"
        )
        return self._write(
            AuditEvent(
                event=event_name,
                contact_key=contact_key,
                contact_id=contact_id,
                masked_phone=mask_phone(phone),
                old_role=CanonicalRole.parse(old_role).value,
                new_role=CanonicalRole.parse(old_role).value,
                role_source=RoleSource.parse(previous_source).value,
                previous_source=RoleSource.parse(previous_source).value,
                trigger=trigger,
                timestamp=_now(),
                candidate_role=CanonicalRole.parse(candidate_role).value,
                accepted_role=CanonicalRole.parse(old_role).value,
                priority=priority or reason,
                reason=reason,
            )
        )
