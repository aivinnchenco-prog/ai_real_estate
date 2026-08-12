"""Controlled inbox watchers for Facebook Messenger / Airbnb Messages.

Only open OwnerRequests are scanned. Replies go through process_owner_message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent7_envoy.messaging.browser_common import message_fingerprint
from agent7_envoy.owner_request_store import OwnerRequest, OwnerRequestStore


@dataclass
class InboundOwnerMessage:
    channel: str
    text: str
    message_id: str
    external_thread_id: str = ""
    owner_request_id: str = ""
    object_id: str = ""
    client_session_chat_id: str = ""
    is_agent7_outbound: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WatcherResult:
    processed: int = 0
    skipped_duplicate: int = 0
    skipped_unrelated: int = 0
    skipped_human_outbound: int = 0
    ambiguous: int = 0
    notes: list[str] = field(default_factory=list)


def classify_message_ownership(
    *,
    text: str,
    known_outbound: str,
    direction: str = "inbound",
) -> str:
    """Return agent7_outbound | human_outbound | inbound | ambiguous."""
    if direction == "outbound":
        if known_outbound and text.strip() == known_outbound.strip():
            return "agent7_outbound"
        if known_outbound and known_outbound.strip() in text:
            return "agent7_outbound"
        return "human_outbound"
    return "inbound"


def stable_message_id(
    *,
    channel: str,
    thread_id: str,
    text: str,
    timestamp: str = "",
    external_id: str = "",
) -> str:
    if external_id:
        return f"{channel}:{external_id}"
    fp = message_fingerprint(text, ts=timestamp)
    return f"{channel}:{thread_id}:{fp}"


class SourceNativeInboxWatcher:
    """Generic watcher: fetch candidates → correlate → process_owner_message."""

    def __init__(
        self,
        *,
        channel: str,
        request_store: OwnerRequestStore | None = None,
        fetch_candidates: Callable[[list[OwnerRequest]], list[dict[str, Any]]] | None = None,
        process_reply: Callable[[InboundOwnerMessage, OwnerRequest], Any] | None = None,
    ):
        self.channel = channel
        self.request_store = request_store or OwnerRequestStore()
        self.fetch_candidates = fetch_candidates or (lambda _reqs: [])
        self.process_reply = process_reply

    def poll(self) -> WatcherResult:
        result = WatcherResult()
        open_reqs = self.request_store.list_open_for_channel(self.channel)
        if not open_reqs:
            result.notes.append("no_open_requests")
            return result

        by_thread: dict[str, list[OwnerRequest]] = {}
        for req in open_reqs:
            key = req.external_thread_id or req.source_url or req.owner_request_id
            by_thread.setdefault(key, []).append(req)

        candidates = self.fetch_candidates(open_reqs)
        for raw in candidates:
            thread_id = str(raw.get("thread_id") or "")
            text = str(raw.get("text") or "")
            ts = str(raw.get("timestamp") or "")
            direction = str(raw.get("direction") or "inbound")
            external_id = str(raw.get("external_id") or "")

            matches = []
            if thread_id:
                matches = [
                    r
                    for r in open_reqs
                    if r.external_thread_id and r.external_thread_id == thread_id
                ]
            if not matches and thread_id:
                matches = by_thread.get(thread_id, [])
            if not matches:
                # Try source_url correlation
                src = str(raw.get("source_url") or "")
                if src:
                    matches = [r for r in open_reqs if r.source_url == src]
            if not matches:
                result.skipped_unrelated += 1
                continue
            if len(matches) > 1:
                result.ambiguous += 1
                result.notes.append(f"ambiguous_thread:{thread_id}")
                continue

            req = matches[0]
            ownership = classify_message_ownership(
                text=text,
                known_outbound=req.outbound_message,
                direction=direction,
            )
            if ownership == "human_outbound":
                result.skipped_human_outbound += 1
                # Do not auto follow-up on ambiguous human interference
                continue
            if ownership == "agent7_outbound":
                continue

            mid = stable_message_id(
                channel=self.channel,
                thread_id=thread_id or req.owner_request_id,
                text=text,
                timestamp=ts,
                external_id=external_id,
            )
            if self.request_store.already_processed(req.owner_request_id, mid):
                result.skipped_duplicate += 1
                continue

            inbound = InboundOwnerMessage(
                channel=self.channel,
                text=text,
                message_id=mid,
                external_thread_id=thread_id or req.external_thread_id,
                owner_request_id=req.owner_request_id,
                object_id=req.object_id,
                client_session_chat_id=req.client_session_chat_id,
                raw=raw,
            )
            if self.process_reply is not None:
                self.process_reply(inbound, req)
            else:
                # Default: mark processed only (tests / dry wiring)
                self.request_store.mark_message_processed(
                    req.owner_request_id,
                    mid,
                    last_seen_external_message=mid,
                )
            result.processed += 1
        return result


def facebook_inbox_watcher(
    *,
    request_store: OwnerRequestStore | None = None,
    fetch_candidates=None,
    process_reply=None,
) -> SourceNativeInboxWatcher:
    return SourceNativeInboxWatcher(
        channel="facebook_messenger",
        request_store=request_store,
        fetch_candidates=fetch_candidates,
        process_reply=process_reply,
    )


def airbnb_inbox_watcher(
    *,
    request_store: OwnerRequestStore | None = None,
    fetch_candidates=None,
    process_reply=None,
) -> SourceNativeInboxWatcher:
    return SourceNativeInboxWatcher(
        channel="airbnb_messages",
        request_store=request_store,
        fetch_candidates=fetch_candidates,
        process_reply=process_reply,
    )
