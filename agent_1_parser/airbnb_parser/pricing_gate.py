"""Global Airbnb pricing gate: max 1 concurrent request, primary before background."""

from __future__ import annotations

import itertools
import threading
from queue import PriorityQueue
from typing import Callable, TypeVar

T = TypeVar("T")

_seq = itertools.count()
_started = False
_start_lock = threading.Lock()
_queue: PriorityQueue = PriorityQueue()

_active_count = 0
_max_concurrent = 0
_instr_lock = threading.Lock()


class _WorkItem:
    __slots__ = ("priority", "seq", "fn", "done", "result", "error")

    def __init__(self, priority: int, seq: int, fn: Callable[[], T]):
        self.priority = priority
        self.seq = seq
        self.fn = fn
        self.done = threading.Event()
        self.result: T | None = None
        self.error: BaseException | None = None

    def __lt__(self, other: "_WorkItem") -> bool:
        return (self.priority, self.seq) < (other.priority, other.seq)


def _ensure_dispatcher() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        threading.Thread(
            target=_dispatch_loop,
            daemon=True,
            name="pricing-global-gate",
        ).start()
        _started = True


def _dispatch_loop() -> None:
    global _active_count, _max_concurrent
    while True:
        item: _WorkItem = _queue.get()
        with _instr_lock:
            _active_count += 1
            _max_concurrent = max(_max_concurrent, _active_count)
        try:
            item.result = item.fn()
        except BaseException as exc:
            item.error = exc
        finally:
            with _instr_lock:
                _active_count -= 1
            item.done.set()


def run_pricing(fn: Callable[[], T], *, primary: bool = False) -> T:
    """Execute one Airbnb pricing network action (global concurrency = 1)."""
    _ensure_dispatcher()
    priority = 0 if primary else 1
    item = _WorkItem(priority, next(_seq), fn)
    _queue.put(item)
    item.done.wait()
    if item.error is not None:
        raise item.error
    return item.result  # type: ignore[return-value]


def reset_instrumentation() -> None:
    global _max_concurrent
    with _instr_lock:
        _max_concurrent = 0
        _active_count = 0


def get_max_concurrent() -> int:
    with _instr_lock:
        return _max_concurrent


def get_active_count() -> int:
    with _instr_lock:
        return _active_count
