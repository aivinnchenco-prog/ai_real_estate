"""Persistent conversation ownership for WhatsApp notify gates."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from agent6_qualifier.messaging.ownership import ConversationOwnershipState
from agent6_qualifier.messaging.types import ConversationOwner
from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits


def default_ownership_store_path() -> Path:
    override = (os.getenv("WAZZUP_OWNERSHIP_STORE_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return (
        Path(__file__).resolve().parents[3] / "data" / "wa_ownership.json"
    )


def ownership_key_for(*, chat_id: str = "", phone: str = "") -> str:
    digits = normalize_phone_e164_digits(phone or chat_id)
    if digits:
        return f"wa:{digits}"
    raw = (chat_id or phone or "").strip()
    return f"chat:{raw}" if raw else ""


class OwnershipStore:
    """File-backed ownership keyed by WA phone / chat id."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_ownership_store_path()
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"conversations": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"conversations": {}}
        if not isinstance(data, dict):
            return {"conversations": {}}
        if not isinstance(data.get("conversations"), dict):
            data["conversations"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def get(
        self, *, chat_id: str = "", phone: str = ""
    ) -> ConversationOwnershipState | None:
        key = ownership_key_for(chat_id=chat_id, phone=phone)
        if not key:
            return None
        with self._lock:
            raw = self._read()["conversations"].get(key)
        if not isinstance(raw, dict):
            return None
        owner_raw = str(raw.get("owner") or ConversationOwner.BOT_ACTIVE.value)
        try:
            owner = ConversationOwner(owner_raw)
        except ValueError:
            owner = ConversationOwner.BOT_ACTIVE
        return ConversationOwnershipState(
            chat_id=str(raw.get("chat_id") or chat_id or phone),
            owner=owner,
            reason=str(raw.get("reason") or ""),
            manager_takeover=bool(raw.get("manager_takeover")),
            last_human_message_id=raw.get("last_human_message_id"),
            known_bot_outbound_ids=set(raw.get("known_bot_outbound_ids") or []),
        )

    def save(self, state: ConversationOwnershipState, *, phone: str = "") -> None:
        key = ownership_key_for(chat_id=state.chat_id, phone=phone)
        if not key:
            return
        payload = {
            "chat_id": state.chat_id,
            "owner": state.owner.value,
            "reason": state.reason,
            "manager_takeover": state.manager_takeover,
            "last_human_message_id": state.last_human_message_id,
            "known_bot_outbound_ids": sorted(state.known_bot_outbound_ids),
        }
        with self._lock:
            data = self._read()
            data["conversations"][key] = payload
            self._write(data)
