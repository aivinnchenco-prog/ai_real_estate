"""Process-level browser/calendar concurrency for VPS-safe batch runs."""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator

logger = logging.getLogger(__name__)

_metrics_lock = threading.Lock()
_calendar_semaphore: threading.Semaphore | None = None
_browser_semaphore: threading.Semaphore | None = None
_price_semaphore: threading.Semaphore | None = None
_object_semaphore: threading.Semaphore | None = None
_active_browsers = 0
_peak_browsers = 0
_calendar_slot_holder: str | None = None
_batch_local = threading.local()


@dataclass
class BatchContext:
    object_id: str
    metrics: BatchRunMetrics | None = None


def set_batch_context(object_id: str, metrics: BatchRunMetrics | None = None) -> None:
    _batch_local.ctx = BatchContext(object_id=object_id, metrics=metrics)


def clear_batch_context() -> None:
    _batch_local.ctx = None


def current_batch_context() -> BatchContext | None:
    return getattr(_batch_local, "ctx", None)


@dataclass
class ServerConcurrencyLimits:
    object_concurrency: int = 1
    calendar_concurrency: int = 1
    price_concurrency: int = 1
    browser_max_instances: int = 2


@dataclass
class BatchRunMetrics:
    object_ids: list[str] = field(default_factory=list)
    per_object_elapsed_s: dict[str, float] = field(default_factory=dict)
    calendar_failures: int = 0
    calendar_failure_objects: list[str] = field(default_factory=list)
    access_denied_count: int = 0
    browser_crash_count: int = 0
    pricing_failures: int = 0
    notion_write_failures: int = 0
    peak_browser_instances: int = 0
    peak_ram_mb: float = 0.0
    errors: list[str] = field(default_factory=list)

    def record_error(self, msg: str) -> None:
        self.errors.append(msg)
        if "access denied" in msg.lower():
            self.access_denied_count += 1
        if "browser" in msg.lower() and ("crash" in msg.lower() or "завис" in msg.lower()):
            self.browser_crash_count += 1


def _sample_ram_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        try:
            import resource

            usage = resource.getrusage(resource.RUSAGE_SELF)
            # Linux: KB; macOS: bytes
            rss = usage.ru_maxrss
            if rss > 10_000_000:
                return rss / (1024 * 1024)
            return rss / 1024
        except Exception:
            return 0.0


def _touch_ram_peak(metrics: BatchRunMetrics | None) -> None:
    if metrics is None:
        return
    sample = _sample_ram_mb()
    if sample > metrics.peak_ram_mb:
        metrics.peak_ram_mb = round(sample, 1)


def configure_server_concurrency(limits: ServerConcurrencyLimits) -> None:
    global _calendar_semaphore, _browser_semaphore, _price_semaphore, _object_semaphore
    cal = max(1, limits.calendar_concurrency)
    if cal != 1:
        logger.warning(
            "calendar_concurrency=%s — SAFE SERVER MODE expects 1; forcing single-flight",
            cal,
        )
        cal = 1
    _calendar_semaphore = threading.Semaphore(1)
    _browser_semaphore = threading.Semaphore(max(1, limits.browser_max_instances))
    _price_semaphore = threading.Semaphore(max(1, limits.price_concurrency))
    _object_semaphore = threading.Semaphore(max(1, limits.object_concurrency))


def _require_configured() -> None:
    if _calendar_semaphore is None or _browser_semaphore is None:
        raise RuntimeError("server concurrency not configured — call configure_server_concurrency()")


@contextmanager
def object_processing_slot(
    object_id: str,
    metrics: BatchRunMetrics | None = None,
) -> Generator[None, None, None]:
    _require_configured()
    assert _object_semaphore is not None
    logger.info("object slot wait: %s", object_id)
    _object_semaphore.acquire()
    logger.info("object slot acquired: %s", object_id)
    start = time.perf_counter()
    try:
        _touch_ram_peak(metrics)
        yield
    finally:
        elapsed = time.perf_counter() - start
        if metrics is not None:
            metrics.per_object_elapsed_s[object_id] = round(elapsed, 2)
        _object_semaphore.release()
        logger.info("object slot released: %s (%.2fs)", object_id, elapsed)


@contextmanager
def calendar_fetch_slot(
    object_id: str,
    metrics: BatchRunMetrics | None = None,
) -> Generator[None, None, None]:
    """Single-flight calendar fetch — no two objects' calendar browsers concurrently."""
    _require_configured()
    assert _calendar_semaphore is not None
    logger.info("calendar slot wait: %s", object_id)
    _calendar_semaphore.acquire()
    global _calendar_slot_holder
    _calendar_slot_holder = object_id
    logger.info("calendar slot acquired: %s", object_id)
    try:
        with browser_instance_slot("calendar", object_id, metrics=metrics):
            _touch_ram_peak(metrics)
            yield
    finally:
        _calendar_slot_holder = None
        _calendar_semaphore.release()
        logger.info("calendar slot released: %s", object_id)


@contextmanager
def price_fetch_slot(
    object_id: str,
    metrics: BatchRunMetrics | None = None,
) -> Generator[None, None, None]:
    _require_configured()
    assert _price_semaphore is not None
    logger.info("price slot wait: %s", object_id)
    _price_semaphore.acquire()
    logger.info("price slot acquired: %s", object_id)
    try:
        with browser_instance_slot("price", object_id, metrics=metrics):
            _touch_ram_peak(metrics)
            yield
    finally:
        _price_semaphore.release()
        logger.info("price slot released: %s", object_id)


@contextmanager
def browser_instance_slot(
    kind: str,
    object_id: str,
    metrics: BatchRunMetrics | None = None,
) -> Generator[None, None, None]:
    _require_configured()
    assert _browser_semaphore is not None
    logger.info("browser slot wait (%s): %s", kind, object_id)
    _browser_semaphore.acquire()
    global _active_browsers, _peak_browsers
    with _metrics_lock:
        _active_browsers += 1
        _peak_browsers = max(_peak_browsers, _active_browsers)
        peak = _peak_browsers
    if metrics is not None:
        metrics.peak_browser_instances = max(metrics.peak_browser_instances, peak)
    logger.info(
        "browser slot acquired (%s): %s active=%d peak=%d holder=%s",
        kind,
        object_id,
        _active_browsers,
        peak,
        _calendar_slot_holder,
    )
    try:
        _touch_ram_peak(metrics)
        yield
    finally:
        with _metrics_lock:
            _active_browsers = max(0, _active_browsers - 1)
        _browser_semaphore.release()
        logger.info("browser slot released (%s): %s", kind, object_id)


def get_peak_browser_instances() -> int:
    return _peak_browsers


def reset_peak_browser_instances() -> None:
    global _peak_browsers, _active_browsers
    with _metrics_lock:
        _peak_browsers = 0
        _active_browsers = 0


def is_server_concurrency_configured() -> bool:
    return _calendar_semaphore is not None


def recommend_price_concurrency(metrics: BatchRunMetrics) -> str:
    """Heuristic post-batch note — does not change config."""
    if metrics.calendar_failures > 0 or metrics.access_denied_count > 0:
        return (
            "KEEP price_concurrency=1: calendar failures or Access Denied observed. "
            "Retry failed objects before raising concurrency."
        )
    if metrics.browser_crash_count > 0:
        return (
            "KEEP price_concurrency=1: browser crashes observed. "
            "Stabilize Selenium/Playwright on VPS first."
        )
    if metrics.peak_browser_instances >= 2:
        return (
            "KEEP price_concurrency=1: peak browser instances already at cap (2). "
            "Raising PRICE concurrency would likely exceed AVAILABILITY_BROWSER_MAX_INSTANCES=2."
        )
    if metrics.peak_ram_mb > 0 and metrics.peak_ram_mb > 3500:
        return (
            f"KEEP price_concurrency=1: peak RAM ~{metrics.peak_ram_mb:.0f} MB is high for this VPS. "
            "Consider PRICE=2 only after monitoring on a quiet window."
        )
    avg = (
        sum(metrics.per_object_elapsed_s.values()) / len(metrics.per_object_elapsed_s)
        if metrics.per_object_elapsed_s
        else 0
    )
    return (
        f"MAY TEST price_concurrency=2 on next batch (calendar_concurrency stays 1): "
        f"batch completed cleanly, peak_browsers={metrics.peak_browser_instances}, "
        f"peak_ram_mb={metrics.peak_ram_mb:.0f}, avg_object_s={avg:.0f}. "
        "Run a second 5-object batch with PRICE=2 only after manual approval — do not auto-raise."
    )
