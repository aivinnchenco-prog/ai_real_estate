"""Persistent mapping: OwnerRequest thread ↔ amo custom conversation."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

OwnerThreadOwnership = Literal["AGENT7_ACTIVE", "HUMAN_ACTIVE", "PAUSED"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_mirror_store_path() -> Path:
    override = (os.getenv("AMO_CHAT_MIRROR_STORE_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "data" / "amo_chat_mirrors.json"


@dataclass
class AmoChatMirrorRecord:
    channel: str
    owner_request_id: str
    conversation_id: str
    external_thread_id: str = ""
    object_id: str = ""
    source_url: str = ""
    amo_conversation_ref_id: str = ""
    scope_id: str = ""
    last_inbound_external_message_id: str = ""
    last_outbound_external_message_id: str = ""
    last_amo_message_id: str = ""
    imported_msgids: list[str] = field(default_factory=list)
    sync_state: str = "READY"  # READY | DEGRADED | BLOCKED
    ownership: OwnerThreadOwnership = "AGENT7_ACTIVE"
    updated_at: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AmoChatMirrorRecord":
        return cls(
            channel=str(data.get("channel") or ""),
            owner_request_id=str(data.get("owner_request_id") or ""),
            conversation_id=str(data.get("conversation_id") or ""),
            external_thread_id=str(data.get("external_thread_id") or ""),
            object_id=str(data.get("object_id") or ""),
            source_url=str(data.get("source_url") or ""),
            amo_conversation_ref_id=str(data.get("amo_conversation_ref_id") or ""),
            scope_id=str(data.get("scope_id") or ""),
            last_inbound_external_message_id=str(
                data.get("last_inbound_external_message_id") or ""
            ),
            last_outbound_external_message_id=str(
                data.get("last_outbound_external_message_id") or ""
            ),
            last_amo_message_id=str(data.get("last_amo_message_id") or ""),
            imported_msgids=list(data.get("imported_msgids") or []),
            sync_state=str(data.get("sync_state") or "READY"),
            ownership=str(data.get("ownership") or "AGENT7_ACTIVE"),  # type: ignore[arg-type]
            updated_at=str(data.get("updated_at") or ""),
            created_at=str(data.get("created_at") or ""),
        )


class AmoChatMirrorStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_mirror_store_path()
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"mirrors": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"mirrors": {}}
        if not isinstance(data, dict):
            return {"mirrors": {}}
        if not isinstance(data.get("mirrors"), dict):
            data["mirrors"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tmp.replace(self.path)

    def upsert(self, rec: AmoChatMirrorRecord) -> AmoChatMirrorRecord:
        now = _now()
        if not rec.created_at:
            rec.created_at = now
        rec.updated_at = now
        with self._lock:
            data = self._read()
            data["mirrors"][rec.conversation_id] = rec.to_dict()
            self._write(data)
        return rec

    def get_by_conversation(self, conversation_id: str) -> AmoChatMirrorRecord | None:
        if not conversation_id:
            return None
        with self._lock:
            raw = self._read()["mirrors"].get(conversation_id)
        return AmoChatMirrorRecord.from_dict(raw) if isinstance(raw, dict) else None

    def get_by_owner_request(self, owner_request_id: str) -> AmoChatMirrorRecord | None:
        if not owner_request_id:
            return None
        with self._lock:
            raws = list(self._read()["mirrors"].values())
        for raw in raws:
            if not isinstance(raw, dict):
                continue
            rec = AmoChatMirrorRecord.from_dict(raw)
            if rec.owner_request_id == owner_request_id:
                return rec
        return None

    def get_by_external_thread(
        self, *, channel: str, external_thread_id: str
    ) -> AmoChatMirrorRecord | None:
        if not external_thread_id:
            return None
        with self._lock:
            raws = list(self._read()["mirrors"].values())
        for raw in raws:
            if not isinstance(raw, dict):
                continue
            rec = AmoChatMirrorRecord.from_dict(raw)
            if rec.channel == channel and rec.external_thread_id == external_thread_id:
                return rec
        return None

    def already_imported(self, conversation_id: str, msgid: str) -> bool:
        rec = self.get_by_conversation(conversation_id)
        if rec is None or not msgid:
            return False
        return msgid in rec.imported_msgids

    def mark_imported(
        self,
        conversation_id: str,
        msgid: str,
        *,
        direction: str = "",
        external_message_id: str = "",
        sync_state: str = "",
    ) -> AmoChatMirrorRecord | None:
        rec = self.get_by_conversation(conversation_id)
        if rec is None:
            return None
        if msgid and msgid not in rec.imported_msgids:
            rec.imported_msgids.append(msgid)
        rec.last_amo_message_id = msgid
        if direction == "inbound" and external_message_id:
            rec.last_inbound_external_message_id = external_message_id
        if direction == "outbound" and external_message_id:
            rec.last_outbound_external_message_id = external_message_id
        if sync_state:
            rec.sync_state = sync_state
        return self.upsert(rec)

    def set_ownership(
        self, conversation_id: str, ownership: OwnerThreadOwnership
    ) -> AmoChatMirrorRecord | None:
        rec = self.get_by_conversation(conversation_id)
        if rec is None:
            return None
        rec.ownership = ownership
        return self.upsert(rec)

    def mark_degraded(self, conversation_id: str) -> AmoChatMirrorRecord | None:
        rec = self.get_by_conversation(conversation_id)
        if rec is None:
            return None
        rec.sync_state = "DEGRADED"
        return self.upsert(rec)
