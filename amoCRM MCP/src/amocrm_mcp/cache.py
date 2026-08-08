"""Small TTL cache for static amoCRM metadata."""

from __future__ import annotations

import time
from typing import Any, Callable


class TtlCache:
    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get_or_set(self, key: str, factory: Callable[[], Any]) -> Any:
        now = time.time()
        item = self._store.get(key)
        if item and now - item[0] < self.ttl_seconds:
            return item[1]
        value = factory()
        self._store[key] = (now, value)
        return value

    def clear(self) -> None:
        self._store.clear()
