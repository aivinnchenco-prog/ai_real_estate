from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int,
    pause_seconds: float = 0.3,
    should_retry: Callable[[Exception], bool] | None = None,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - controlled retry wrapper
            last_exc = exc
            if should_retry and not should_retry(exc):
                raise
            if attempt + 1 >= attempts:
                raise
            time.sleep(pause_seconds)
    assert last_exc is not None
    raise last_exc


def retry_find(
    fn: Callable[[], T | None],
    *,
    attempts: int,
    pause_seconds: float = 0.3,
) -> T | None:
    for attempt in range(max(1, attempts)):
        result = fn()
        if result is not None:
            return result
        if attempt + 1 < attempts:
            time.sleep(pause_seconds)
    return None
