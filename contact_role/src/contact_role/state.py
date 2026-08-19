"""Persistent ContactRoleState store (shared by Agent 6 / Agent 7)."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .phone import contact_key_for, normalize_phone_e164
from .roles import CanonicalRole
from .sources import RoleSource


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_store_path() -> Path:
    override = (os.getenv("CONTACT_ROLE_STORE_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    root = Path(__file__).resolve().parents[3]
    return root / "data" / "contact_roles.json"


@dataclass
class ContactRoleState:
    contact_key: str
    phone: str = ""
    canonical_role: str = CanonicalRole.UNKNOWN.value
    role_source: str = RoleSource.UNKNOWN.value
    role_confidence: float | None = None
    role_set_at: str = ""
    role_updated_at: str = ""
    locked_by_manual_override: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContactRoleState:
        return cls(
            contact_key=str(data.get("contact_key") or ""),
            phone=str(data.get("phone") or ""),
            canonical_role=str(
                data.get("canonical_role") or CanonicalRole.UNKNOWN.value
            ),
            role_source=str(data.get("role_source") or RoleSource.UNKNOWN.value),
            role_confidence=(
                float(data["role_confidence"])
                if data.get("role_confidence") is not None
                else None
            ),
            role_set_at=str(data.get("role_set_at") or ""),
            role_updated_at=str(data.get("role_updated_at") or ""),
            locked_by_manual_override=bool(data.get("locked_by_manual_override")),
            metadata=dict(data.get("metadata") or {}),
        )


class ContactRoleStore:
    """JSON file store keyed by contact_key."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_store_path()
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"contacts": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"contacts": {}}
        if not isinstance(data, dict):
            return {"contacts": {}}
        contacts = data.get("contacts")
        if not isinstance(contacts, dict):
            contacts = {}
        return {"contacts": contacts}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def get(self, contact_key: str) -> ContactRoleState | None:
        with self._lock:
            raw = self._read()["contacts"].get(contact_key)
        if not raw or not isinstance(raw, dict):
            return None
        return ContactRoleState.from_dict(raw)

    def get_by_phone(self, phone: str | None) -> ContactRoleState | None:
        phone_n = normalize_phone_e164(phone)
        if not phone_n:
            return None
        key = contact_key_for(phone=phone_n)
        assert key is not None
        found = self.get(key)
        if found:
            return found
        # Legacy scan by phone field
        with self._lock:
            contacts = self._read()["contacts"]
        for raw in contacts.values():
            if not isinstance(raw, dict):
                continue
            if normalize_phone_e164(raw.get("phone")) == phone_n:
                return ContactRoleState.from_dict(raw)
        return None

    def upsert(self, state: ContactRoleState) -> ContactRoleState:
        now = _now()
        if not state.role_set_at:
            state.role_set_at = now
        state.role_updated_at = now
        with self._lock:
            data = self._read()
            data["contacts"][state.contact_key] = state.to_dict()
            self._write(data)
        return state

    def clear(self) -> None:
        with self._lock:
            self._write({"contacts": {}})
