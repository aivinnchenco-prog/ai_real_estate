"""Durable local JSON state for Agent 9 conversations."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import data_dir
from .state_machine import BusinessState


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ConversationState:
    object_id: str
    notion_page_id: str = ""
    facebook_url: str = ""
    listing_id: str = ""
    thread_id: str = ""
    thread_url: str = ""
    state: str = BusinessState.NEW.value
    outreach_status: str = "pending"
    intro_sent_at: str | None = None
    role_question_sent_at: str | None = None
    follow_up_sent_at: str | None = None
    last_inbound_at: str | None = None
    whatsapp_raw: str | None = None
    whatsapp_normalized: str | None = None
    role: str | None = None
    attempt: int = 1
    last_error: str | None = None
    property_type: str = ""
    screen_state: str = "UNKNOWN"
    whatsapp_candidate_conflict: str | None = None
    whatsapp_existing: str = ""
    owner_agent_existing: str = ""
    seen_inbound_snippets: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=_now_iso)

    def touch(self) -> None:
        self.updated_at = _now_iso()


_CONV_FIELDS = {f.name for f in fields(ConversationState)}


def conversation_from_dict(raw: dict) -> ConversationState:
    data = {k: v for k, v in raw.items() if k in _CONV_FIELDS}
    if "seen_inbound_snippets" not in data:
        data["seen_inbound_snippets"] = []
    return ConversationState(**data)


class StateStore:
    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()
        self.path = self.root / "conversations.json"
        self.queue_path = self.root / "queue.json"
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_conversations({})

    def _read_conversations(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _write_conversations(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, object_id: str) -> ConversationState | None:
        raw = self._read_conversations().get(object_id)
        if not raw:
            return None
        return conversation_from_dict(raw)

    def save(self, state: ConversationState) -> None:
        with self._lock:
            data = self._read_conversations()
            state.touch()
            data[state.object_id] = asdict(state)
            self._write_conversations(data)

    def all_conversations(self) -> list[ConversationState]:
        return [conversation_from_dict(v) for v in self._read_conversations().values()]

    def listing_id_blocked(self, listing_id: str, *, exclude_object_id: str = "") -> tuple[bool, str]:
        """One Facebook listing = one outreach conversation."""
        if not listing_id:
            return False, ""
        for raw in self._read_conversations().values():
            oid = raw.get("object_id", "")
            if oid == exclude_object_id:
                continue
            if raw.get("listing_id") == listing_id:
                return True, f"conversation:{oid}:{raw.get('state')}"
        queue = self._read_queue()
        for oid, item in queue.items():
            if oid == exclude_object_id:
                continue
            if item.get("listing_id") == listing_id:
                return True, f"queue:{oid}"
        return False, ""

    def list_active(self) -> list[ConversationState]:
        from .state_machine import ACTIVE_CONVERSATION_STATES
        active = {s.value for s in ACTIVE_CONVERSATION_STATES}
        active.add(BusinessState.INTRO_SENT.value)
        active.add(BusinessState.CONTACT_RECEIVED.value)
        active.add(BusinessState.ROLE_ASKED.value)
        return [
            conversation_from_dict(v) for v in self._read_conversations().values()
            if v.get("state") in active
        ]

    def enqueue(self, object_id: str, payload: dict) -> bool:
        with self._lock:
            if object_id in self._read_conversations():
                return False
            listing_id = (payload.get("listing_id") or "").strip()
            if listing_id:
                blocked, _ = self.listing_id_blocked(listing_id, exclude_object_id=object_id)
                if blocked:
                    return False
            queue = self._read_queue()
            if object_id in queue:
                return False
            for item in queue.values():
                if listing_id and item.get("listing_id") == listing_id:
                    return False
            queue[object_id] = {**payload, "enqueued_at": _now_iso()}
            self._write_queue(queue)
            return True

    def dequeue(self, object_id: str) -> dict | None:
        with self._lock:
            queue = self._read_queue()
            item = queue.pop(object_id, None)
            self._write_queue(queue)
            return item

    def read_queue(self) -> dict:
        return self._read_queue()

    def sanitize_queue(self) -> int:
        """Remove queue rows already in conversations or duplicate listing_id."""
        removed = 0
        with self._lock:
            conversations = self._read_conversations()
            queue = self._read_queue()
            kept: dict[str, dict] = {}
            seen_listings: set[str] = set()
            for oid, item in queue.items():
                if oid in conversations:
                    removed += 1
                    continue
                lid = (item.get("listing_id") or "").strip()
                if lid:
                    dup_in_conv = any(
                        v.get("listing_id") == lid and v.get("object_id") != oid
                        for v in conversations.values()
                    )
                    if dup_in_conv or lid in seen_listings:
                        removed += 1
                        continue
                    seen_listings.add(lid)
                kept[oid] = item
            if removed:
                self._write_queue(kept)
        return removed

    def _read_queue(self) -> dict:
        try:
            return json.loads(self.queue_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _write_queue(self, data: dict) -> None:
        self.queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
