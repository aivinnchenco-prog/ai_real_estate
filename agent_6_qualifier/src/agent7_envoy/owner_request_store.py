"""Minimal durable owner-request correlation for Agent7 (WA/TG/FB/Airbnb).

Links: owner destination ↔ object_id ↔ client session ↔ request id.
Lifecycle: CREATED → SENT → AWAITING_OWNER → OWNER_REPLY_RECEIVED → RESOLVED
Also: UNKNOWN_SEND_STATE when send outcome is uncertain.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits

OwnerRequestStatus = Literal[
    "CREATED",
    "SENT",
    "AWAITING_OWNER",
    "OWNER_REPLY_RECEIVED",
    "RESOLVED",
    "UNKNOWN_SEND_STATE",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_owner_request_path() -> Path:
    override = (os.getenv("AGENT7_OWNER_REQUEST_STORE_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "owner_requests.json"


def new_owner_request_id() -> str:
    return f"orq-{uuid.uuid4()}"


def outbound_idempotency_key(owner_request_id: str, channel: str) -> str:
    return f"{owner_request_id}:{channel}"


@dataclass
class OwnerRequest:
    owner_request_id: str
    owner_phone: str
    object_id: str
    client_session_chat_id: str
    status: OwnerRequestStatus = "CREATED"
    channel: str = "whatsapp"
    created_at: str = ""
    updated_at: str = ""
    resolved_at: str = ""
    processed_owner_message_ids: list[str] = field(default_factory=list)
    last_verdict: str = ""
    # Source-native / shared correlation fields
    source: str = ""  # FACEBOOK | AIRBNB | WHATSAPP | TELEGRAM | …
    source_url: str = ""
    external_thread_id: str = ""
    external_conversation_url: str = ""
    external_message_id: str = ""
    last_seen_external_message: str = ""
    outbound_message: str = ""
    sent_at: str = ""
    outbound_key: str = ""
    send_outcome: str = ""  # sent | dry_run | unknown | blocked | …
    agent7_owned_outbound: bool = True
    # Structured business result (persisted BEFORE CRM sync)
    owner_result: dict[str, Any] = field(default_factory=dict)
    crm_visibility: str = ""
    crm_business_sync: str = ""  # SYNCED | FAILED | SKIPPED_NO_AMO | …

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OwnerRequest":
        return cls(
            owner_request_id=str(data.get("owner_request_id") or ""),
            owner_phone=str(data.get("owner_phone") or ""),
            object_id=str(data.get("object_id") or ""),
            client_session_chat_id=str(data.get("client_session_chat_id") or ""),
            status=str(data.get("status") or "CREATED"),  # type: ignore[arg-type]
            channel=str(data.get("channel") or "whatsapp"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            resolved_at=str(data.get("resolved_at") or ""),
            processed_owner_message_ids=list(
                data.get("processed_owner_message_ids") or []
            ),
            last_verdict=str(data.get("last_verdict") or ""),
            source=str(data.get("source") or ""),
            source_url=str(data.get("source_url") or ""),
            external_thread_id=str(data.get("external_thread_id") or ""),
            external_conversation_url=str(
                data.get("external_conversation_url") or ""
            ),
            external_message_id=str(data.get("external_message_id") or ""),
            last_seen_external_message=str(
                data.get("last_seen_external_message") or ""
            ),
            outbound_message=str(data.get("outbound_message") or ""),
            sent_at=str(data.get("sent_at") or ""),
            outbound_key=str(data.get("outbound_key") or ""),
            send_outcome=str(data.get("send_outcome") or ""),
            agent7_owned_outbound=bool(data.get("agent7_owned_outbound", True)),
            owner_result=dict(data.get("owner_result") or {}),
            crm_visibility=str(data.get("crm_visibility") or ""),
            crm_business_sync=str(data.get("crm_business_sync") or ""),
        )


class OwnerRequestStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_owner_request_path()
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"requests": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"requests": {}}
        if not isinstance(data, dict):
            return {"requests": {}}
        reqs = data.get("requests")
        if not isinstance(reqs, dict):
            data["requests"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def create(
        self,
        *,
        owner_phone: str = "",
        object_id: str,
        client_session_chat_id: str,
        channel: str = "whatsapp",
        owner_request_id: str | None = None,
        source: str = "",
        source_url: str = "",
        outbound_message: str = "",
    ) -> OwnerRequest:
        digits = normalize_phone_e164_digits(owner_phone) or ""
        now = _now()
        rid = owner_request_id or new_owner_request_id()
        req = OwnerRequest(
            owner_request_id=rid,
            owner_phone=digits,
            object_id=object_id,
            client_session_chat_id=client_session_chat_id,
            status="CREATED",
            channel=channel,
            created_at=now,
            updated_at=now,
            source=source or channel.upper(),
            source_url=source_url,
            outbound_message=outbound_message,
            outbound_key=outbound_idempotency_key(rid, channel),
        )
        with self._lock:
            data = self._read()
            # Idempotency: refuse duplicate outbound_key that already sent
            for raw in data["requests"].values():
                if not isinstance(raw, dict):
                    continue
                existing = OwnerRequest.from_dict(raw)
                if (
                    existing.outbound_key == req.outbound_key
                    and existing.status
                    in {
                        "SENT",
                        "AWAITING_OWNER",
                        "OWNER_REPLY_RECEIVED",
                        "RESOLVED",
                        "UNKNOWN_SEND_STATE",
                    }
                ):
                    return existing
            data["requests"][req.owner_request_id] = req.to_dict()
            self._write(data)
        return req

    def get(self, owner_request_id: str) -> OwnerRequest | None:
        if not owner_request_id:
            return None
        with self._lock:
            raw = self._read()["requests"].get(owner_request_id)
        return OwnerRequest.from_dict(raw) if isinstance(raw, dict) else None

    def upsert(self, req: OwnerRequest) -> OwnerRequest:
        req.updated_at = _now()
        with self._lock:
            data = self._read()
            data["requests"][req.owner_request_id] = req.to_dict()
            self._write(data)
        return req

    def find_by_outbound_key(self, outbound_key: str) -> OwnerRequest | None:
        if not outbound_key:
            return None
        with self._lock:
            raws = list(self._read()["requests"].values())
        for raw in raws:
            if not isinstance(raw, dict):
                continue
            req = OwnerRequest.from_dict(raw)
            if req.outbound_key == outbound_key:
                return req
        return None

    def already_sent(self, owner_request_id: str, channel: str) -> bool:
        key = outbound_idempotency_key(owner_request_id, channel)
        req = self.find_by_outbound_key(key) or self.get(owner_request_id)
        if req is None:
            return False
        if req.send_outcome == "unknown" or req.status == "UNKNOWN_SEND_STATE":
            return True  # block blind retry
        return req.status in {
            "SENT",
            "AWAITING_OWNER",
            "OWNER_REPLY_RECEIVED",
            "RESOLVED",
        }

    def mark_sent(
        self,
        owner_request_id: str,
        *,
        external_thread_id: str = "",
        external_conversation_url: str = "",
        external_message_id: str = "",
        outbound_message: str = "",
        send_outcome: str = "sent",
    ) -> OwnerRequest | None:
        req = self.get(owner_request_id)
        if req is None:
            return None
        req.status = "SENT"
        req.sent_at = _now()
        req.send_outcome = send_outcome
        if external_thread_id:
            req.external_thread_id = external_thread_id
        if external_conversation_url:
            req.external_conversation_url = external_conversation_url
        if external_message_id:
            req.external_message_id = external_message_id
        if outbound_message:
            req.outbound_message = outbound_message
        return self.upsert(req)

    def mark_unknown_send(self, owner_request_id: str) -> OwnerRequest | None:
        req = self.get(owner_request_id)
        if req is None:
            return None
        req.status = "UNKNOWN_SEND_STATE"
        req.send_outcome = "unknown"
        return self.upsert(req)

    def mark_awaiting(self, owner_request_id: str) -> OwnerRequest | None:
        req = self.get(owner_request_id)
        if req is None:
            return None
        req.status = "AWAITING_OWNER"
        if not req.sent_at:
            req.sent_at = _now()
        if not req.send_outcome:
            req.send_outcome = "sent"
        return self.upsert(req)

    def list_open_for_owner(self, owner_phone: str) -> list[OwnerRequest]:
        digits = normalize_phone_e164_digits(owner_phone) or ""
        if not digits:
            return []
        with self._lock:
            raws = list(self._read()["requests"].values())
        out: list[OwnerRequest] = []
        for raw in raws:
            if not isinstance(raw, dict):
                continue
            req = OwnerRequest.from_dict(raw)
            if req.owner_phone != digits:
                continue
            if req.status in {"AWAITING_OWNER", "OWNER_REPLY_RECEIVED", "SENT"}:
                out.append(req)
        return out

    def list_open_for_channel(self, channel: str) -> list[OwnerRequest]:
        ch = (channel or "").strip().lower()
        with self._lock:
            raws = list(self._read()["requests"].values())
        out: list[OwnerRequest] = []
        for raw in raws:
            if not isinstance(raw, dict):
                continue
            req = OwnerRequest.from_dict(raw)
            if (req.channel or "").lower() != ch:
                continue
            if req.status in {"AWAITING_OWNER", "OWNER_REPLY_RECEIVED", "SENT"}:
                out.append(req)
        return out

    def list_open_by_thread(self, external_thread_id: str) -> list[OwnerRequest]:
        tid = (external_thread_id or "").strip()
        if not tid:
            return []
        with self._lock:
            raws = list(self._read()["requests"].values())
        out: list[OwnerRequest] = []
        for raw in raws:
            if not isinstance(raw, dict):
                continue
            req = OwnerRequest.from_dict(raw)
            if req.external_thread_id != tid:
                continue
            if req.status in {"AWAITING_OWNER", "OWNER_REPLY_RECEIVED", "SENT"}:
                out.append(req)
        return out

    def list_open_for_client_object(
        self,
        *,
        client_session_chat_id: str,
        object_id: str,
        channel: str = "",
    ) -> list[OwnerRequest]:
        client = str(client_session_chat_id or "")
        oid = str(object_id or "")
        ch = (channel or "").strip().lower()
        if not client or not oid:
            return []
        with self._lock:
            raws = list(self._read()["requests"].values())
        out: list[OwnerRequest] = []
        for raw in raws:
            if not isinstance(raw, dict):
                continue
            req = OwnerRequest.from_dict(raw)
            if req.client_session_chat_id != client:
                continue
            if req.object_id != oid:
                continue
            if ch and (req.channel or "").lower() != ch:
                continue
            if req.status in {
                "CREATED",
                "SENT",
                "AWAITING_OWNER",
                "OWNER_REPLY_RECEIVED",
                "UNKNOWN_SEND_STATE",
            }:
                out.append(req)
        return out

    def find_by_processed_message(
        self, owner_phone: str, message_id: str
    ) -> OwnerRequest | None:
        digits = normalize_phone_e164_digits(owner_phone) or ""
        if not digits or not message_id:
            return None
        with self._lock:
            raws = list(self._read()["requests"].values())
        for raw in raws:
            if not isinstance(raw, dict):
                continue
            req = OwnerRequest.from_dict(raw)
            if req.owner_phone != digits:
                continue
            if message_id in req.processed_owner_message_ids:
                return req
        return None

    def already_processed(self, owner_request_id: str, message_id: str) -> bool:
        req = self.get(owner_request_id)
        if req is None or not message_id:
            return False
        return message_id in req.processed_owner_message_ids

    def mark_message_processed(
        self,
        owner_request_id: str,
        message_id: str,
        *,
        verdict: str = "",
        resolve: bool = False,
        last_seen_external_message: str = "",
    ) -> OwnerRequest | None:
        req = self.get(owner_request_id)
        if req is None:
            return None
        if message_id and message_id not in req.processed_owner_message_ids:
            req.processed_owner_message_ids.append(message_id)
        req.status = "RESOLVED" if resolve else "OWNER_REPLY_RECEIVED"
        if verdict:
            req.last_verdict = verdict
        if last_seen_external_message:
            req.last_seen_external_message = last_seen_external_message
        elif message_id:
            req.last_seen_external_message = message_id
        if resolve:
            req.resolved_at = _now()
        return self.upsert(req)
