"""Background worker: 1 global worker, fair queue, rate limit, per-month save."""

from __future__ import annotations

import random
import threading
import time
from datetime import date

import config
from airbnb_parser import AirbnbParser
from CustomLogger import logger
from monthly_pricing import entry_has_price, price_for_month
from price_cache import save_price_cache
from pricing_config import (
    background_workers,
    block_cooldown_seconds,
    max_job_attempts,
    months_ahead,
    request_delay_seconds,
    retry_delay_seconds,
)
from pricing_calendar import (
    apply_calendar_to_object,
    month_has_calendar_data,
    needs_calendar_refresh,
    refresh_calendar_snapshot,
)
from pricing_gate import run_pricing
from pricing_queue import PricingQueue, get_queue
from pricing_state import (
    compute_pricing_status,
    deserialize_calendar,
    expected_month_keys,
    pricing_months_collected,
    serialize_calendar,
)

_WORKER_THREAD: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()
_STOP = threading.Event()


def ensure_background_worker() -> None:
    if not getattr(config, "PRICE_COLLECT_ENABLED", True):
        return
    global _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return
        count = background_workers()
        _STOP.clear()
        for i in range(count):
            t = threading.Thread(
                target=_worker_loop,
                daemon=True,
                name=f"pricing-bg-worker-{i}",
            )
            t.start()
            if i == 0:
                _WORKER_THREAD = t


def bootstrap_background_pricing() -> bool:
    """Process startup: resume/consume pricing queue (airbnb-bot.service → main.py)."""
    if not getattr(config, "PRICE_COLLECT_ENABLED", True):
        return False
    ensure_background_worker()
    queue = get_queue()
    queue.load()
    pending = len(queue._eligible_jobs())
    logger.info(f"pricing worker bootstrap: pending/retry jobs={pending}")
    return True


def stop_background_worker() -> None:
    _STOP.set()


def _parse_month(month_key: str) -> tuple[int, int]:
    y, m = month_key.split("-")
    return int(y), int(m)


def _maybe_sync_notion(object_id: str, monthly: dict) -> None:
    """Опционально обновляет Notion; в тестах/offline — no-op."""
    if not getattr(config, "PRICE_NOTION_SYNC_ENABLED", True):
        return
    import os

    if not os.getenv("NOTION_API_KEY"):
        return
    queue = get_queue()
    obj = queue.get_object(object_id) or {}
    page_id = (obj.get("notion_page_id") or "").strip()
    if not page_id:
        return
    try:
        from notion_price_sync import write_prices_to_notion

        write_prices_to_notion(page_id, monthly)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning(f"Notion price sync failed for {object_id}: {exc}")


def process_one_job(
    job: dict,
    parser: AirbnbParser | None = None,
    queue: PricingQueue | None = None,
) -> bool:
    """Обрабатывает одну задачу; True если что-то сделано."""
    queue = queue or get_queue()
    queue.load()
    jid = job.get("job_id")
    resolved = next((j for j in queue._data["jobs"] if j.get("job_id") == jid), job)
    job = resolved
    object_id = job["object_id"]
    month = job["month"]
    url = job["listing_url"]

    obj = queue.get_object(object_id)
    if not obj:
        queue.mark_done(job, "done")
        queue.save()
        return False

    monthly = dict(obj.get("monthly_prices") or {})
    if entry_has_price(monthly.get(month)):
        queue.mark_done(job, "done")
        queue.save()
        return False

    availability = deserialize_calendar(obj.get("calendar"))
    year, mon = _parse_month(month)
    fetch_state: dict = {}
    owned_parser = parser is None
    if owned_parser:
        parser = AirbnbParser(headless=True)

    def fetch(check_in, check_out):
        value, blocked = parser._fetch_price_for_period_once(url, check_in, check_out)
        if blocked:
            fetch_state["blocked"] = True
        return value

    def _pricing_work() -> tuple[str, dict | None]:
        nonlocal availability, obj
        should_refresh, reason = needs_calendar_refresh(obj, year, mon)
        if should_refresh:
            new_cal = refresh_calendar_snapshot(url)
            if new_cal:
                apply_calendar_to_object(obj, new_cal)
                availability = new_cal
                queue.upsert_object(
                    object_id,
                    listing_url=url,
                    calendar=obj["calendar"],
                    calendar_saved_at=obj.get("calendar_saved_at"),
                )
            if not month_has_calendar_data(availability, year, mon):
                return "calendar_missing", None
        entry = price_for_month(
            fetch,
            availability,
            year,
            mon,
            min_segment_days=config.PRICE_MIN_SEGMENT_DAYS,
        )
        # insufficient_data при отсутствии calendar coverage — retry, не terminal
        if (
            entry.get("status") == "insufficient_data"
            and not month_has_calendar_data(availability, year, mon)
        ):
            return "calendar_missing", None
        return "ok", entry

    try:
        outcome, entry = run_pricing(_pricing_work, primary=False)
    finally:
        if owned_parser and parser:
            try:
                parser.close()
            except Exception:
                pass

    if outcome == "calendar_missing":
        attempt = int(job.get("attempt") or 0) + 1
        queue.mark_retry(
            job,
            delay_sec=retry_delay_seconds(attempt),
            error="calendar_missing",
        )
        queue.save()
        return True

    if fetch_state.get("blocked"):
        streak = int(obj.get("block_streak") or 0) + 1
        obj["block_streak"] = streak
        queue.mark_retry(
            job,
            delay_sec=block_cooldown_seconds(streak - 1),
            error="blocked",
            as_blocked=True,
        )
        queue.save()
        return True

    obj["block_streak"] = 0
    status = entry.get("status") or "insufficient_data"
    if entry_has_price(entry):
        monthly[month] = entry
        queue.save_month_result(object_id, month, entry)
        save_price_cache(url, monthly)
        queue.mark_done(job, "done")
        _maybe_sync_notion(object_id, monthly)
    elif status == "insufficient_data":
        monthly[month] = entry
        queue.save_month_result(object_id, month, entry)
        queue.mark_done(job, "insufficient_data")
    else:
        attempt = int(job.get("attempt") or 0) + 1
        if attempt >= max_job_attempts():
            monthly[month] = entry
            queue.save_month_result(object_id, month, entry)
            queue.mark_done(job, "insufficient_data")
        else:
            queue.mark_retry(
                job,
                delay_sec=retry_delay_seconds(attempt),
                error=status,
            )

    obj = queue.get_object(object_id) or obj
    monthly = obj.get("monthly_prices") or monthly
    target = int(obj.get("pricing_months_target") or months_ahead())
    expected = obj.get("expected_month_keys") or expected_month_keys(target)
    obj["pricing_status"] = compute_pricing_status(
        monthly,
        months_target=target,
        has_active_jobs=queue.has_active_jobs(object_id),
        expected_keys=expected,
    )
    obj["pricing_months_collected"] = pricing_months_collected(monthly)
    queue.upsert_object(
        object_id,
        listing_url=url,
        monthly_prices=monthly,
        months_target=target,
    )
    queue.save()
    return True


def _worker_loop() -> None:
    lo, hi = request_delay_seconds()
    parser: AirbnbParser | None = None
    try:
        while not _STOP.is_set():
            queue = get_queue()
            queue.load()
            job = queue.pick_next_job()
            if not job:
                time.sleep(2.0)
                continue

            queue.mark_running(job)
            queue.save()

            if parser is None:
                parser = AirbnbParser(headless=True)

            try:
                process_one_job(job, parser=parser)
            except Exception as exc:
                logger.error(f"pricing job {job.get('job_id')}: {exc}")
                queue.mark_retry(
                    job,
                    delay_sec=retry_delay_seconds(int(job.get("attempt") or 0) + 1),
                    error=str(exc),
                )
                queue.save()
                try:
                    parser.close()
                except Exception:
                    pass
                parser = None

            delay = random.uniform(lo, hi)
            time.sleep(delay)
    finally:
        if parser:
            try:
                parser.close()
            except Exception:
                pass


def process_next_job_for_tests(parser=None) -> dict | None:
    """Offline hook: один шаг воркера без sleep."""
    queue = get_queue()
    queue.load()
    job = queue.pick_next_job()
    if not job:
        return None
    queue.mark_running(job)
    queue.save()
    process_one_job(job, parser=parser)
    return job
