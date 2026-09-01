"""Coalesce rapid WhatsApp bubbles into one Qualifier turn."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

from agent6_qualifier.messaging.types import CanonicalInboundMessage

DEFAULT_WINDOW_SEC = 4.0


def coalesce_messages(
    messages: list[CanonicalInboundMessage],
) -> CanonicalInboundMessage:
    """Join inbound texts in arrival order; keep the last message's metadata."""
    if not messages:
        raise ValueError("no messages to coalesce")
    texts = [m.text or "" for m in messages]
    joined = "\n".join(t for t in texts if t).strip()
    last = messages[-1]
    return replace(last, text=joined or last.text)


@dataclass
class InboundDebounceBuffer:
    window_sec: float = DEFAULT_WINDOW_SEC
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._pending: dict[str, list[CanonicalInboundMessage]] = {}
        self._due_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, message: CanonicalInboundMessage) -> None:
        chat_id = message.chat_id or ""
        with self._lock:
            self._pending.setdefault(chat_id, []).append(message)
            self._due_at[chat_id] = self.clock() + float(self.window_sec)

    def remaining(self, chat_id: str) -> float | None:
        with self._lock:
            due = self._due_at.get(chat_id)
        if due is None:
            return None
        return max(0.0, due - self.clock())

    def min_remaining(self) -> float | None:
        with self._lock:
            if not self._due_at:
                return None
            soonest = min(self._due_at.values())
        return max(0.0, soonest - self.clock())

    def pop_ready(self) -> list[list[CanonicalInboundMessage]]:
        now = self.clock()
        ready: list[list[CanonicalInboundMessage]] = []
        with self._lock:
            for chat_id, due in list(self._due_at.items()):
                if now + 1e-9 < due:
                    continue
                group = self._pending.pop(chat_id, [])
                self._due_at.pop(chat_id, None)
                if group:
                    ready.append(group)
        return ready
