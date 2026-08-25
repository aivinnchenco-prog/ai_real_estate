from __future__ import annotations

import fcntl
import logging
import random
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Generator
from urllib.parse import urlparse, urlunparse

from .calendar_matcher import is_calendar_api_payload, is_calendar_api_url
from .calendar_trace import (
    CalendarFetchTrace,
    record_response_event,
    save_failure_artifacts,
)
from .config import mask_proxy_server
from .models import AirbnbWorker, CalendarCheckResult, CheckResult, ProxyConfig
from .page_state import (
    PageKind,
    classify_page_kind,
    detect_page_state,
    extract_listing_id,
    page_kind_to_check_result,
)

logger = logging.getLogger(__name__)

_PROXY_ERROR = re.compile(
    r"proxy|tunnel|407|authentication required|net::err_proxy|connection refused",
    re.I,
)
_CALENDAR_UI_SELECTORS = (
    '[data-testid="book-it-default"]',
    '[data-section-id="BOOK_IT_SIDEBAR"]',
    '[data-testid="inline-availability-calendar"]',
    '[data-testid="change-dates-checkIn"]',
    'button:has-text("Check availability")',
    'button:has-text("Show dates")',
)

_profile_locks: dict[str, threading.Lock] = {}
_profile_locks_guard = threading.Lock()
_worker_pacing_until: dict[str, float] = {}
_worker_semaphores: dict[str, threading.Semaphore] = {}
_pacing_lock = threading.Lock()


@dataclass
class _AttemptResult:
    base: CalendarCheckResult
    captured: list[dict] = field(default_factory=list)
    url: str = ""
    title: str = ""
    body: str = ""
    html: str = ""
    screenshot: bytes | None = None
    trace: CalendarFetchTrace = field(default_factory=CalendarFetchTrace)
    page_kind: PageKind = PageKind.UNKNOWN


def normalize_listing_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.replace("airbnb.ru", "airbnb.com")
    if "airbnb.com" in host and not host.startswith("www."):
        host = "www." + host.lstrip(".")
    path = parsed.path or "/"
    return urlunparse((parsed.scheme or "https", host, path, "", "", ""))


def _profile_thread_lock(profile_path: str) -> threading.Lock:
    with _profile_locks_guard:
        if profile_path not in _profile_locks:
            _profile_locks[profile_path] = threading.Lock()
        return _profile_locks[profile_path]


@contextmanager
def profile_directory_lock(profile_path: Path) -> Generator[None, None, None]:
    profile_path.mkdir(parents=True, exist_ok=True)
    lock_file = profile_path / ".profile.lock"
    lock_file.touch(exist_ok=True)
    thread_lock = _profile_thread_lock(str(profile_path.resolve()))
    with thread_lock:
        with lock_file.open("r+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _worker_semaphore(worker_id: str, max_concurrency: int) -> threading.Semaphore:
    with _pacing_lock:
        sem = _worker_semaphores.get(worker_id)
        if sem is None or getattr(sem, "_max_concurrency", None) != max_concurrency:
            sem = threading.Semaphore(max_concurrency)
            sem._max_concurrency = max_concurrency  # type: ignore[attr-defined]
            _worker_semaphores[worker_id] = sem
        return sem


def wait_worker_pacing(worker_id: str, min_delay: float, max_delay: float) -> None:
    if max_delay <= 0:
        return
    lo = max(0.0, min_delay)
    hi = max(lo, max_delay)
    with _pacing_lock:
        now = time.monotonic()
        ready_at = _worker_pacing_until.get(worker_id, 0.0)
        if now < ready_at:
            time.sleep(ready_at - now)
        delay = random.uniform(lo, hi) if hi > lo else lo
        _worker_pacing_until[worker_id] = time.monotonic() + delay


@contextmanager
def worker_execution_slot(
    worker_id: str,
    *,
    max_concurrency: int,
    min_delay: float,
    max_delay: float,
) -> Generator[None, None, None]:
    sem = _worker_semaphore(worker_id, max_concurrency)
    sem.acquire()
    try:
        wait_worker_pacing(worker_id, min_delay, max_delay)
        yield
    finally:
        sem.release()


def _parse_calendar_payload(captured: list[dict]) -> dict[date, bool]:
    days: dict[date, bool] = {}
    payload = captured[0]
    data = payload.get("data") or {}
    merlin = data.get("merlin") or {}
    node = merlin.get("pdpAvailabilityCalendar") or data.get("pdpAvailabilityCalendar")
    if not isinstance(node, dict):
        node = data.get("staysPdpAvailabilityCalendar") or {}
    months = node.get("calendarMonths") or []
    for m in months:
        for d in m.get("days") or []:
            days[date.fromisoformat(d["calendarDate"])] = bool(d["available"])
    return days


def _maybe_capture_response(resp, captured: list[dict], trace: CalendarFetchTrace) -> bool:
    matched = False
    try:
        if resp.request.method.upper() == "POST":
            body = (resp.request.post_data or "").lower()
            if "pdpavailabilitycalendar" in body or "availabilitycalendar" in body:
                matched = True
        if is_calendar_api_url(resp.url):
            matched = True
        if matched:
            payload = resp.json()
            if is_calendar_api_payload(payload):
                captured.append(payload)
                trace.calendar_responses_captured = len(captured)
                record_response_event(
                    trace,
                    url=resp.url,
                    method=resp.request.method,
                    status=resp.status,
                    matched_calendar=True,
                )
                return True
        if is_calendar_api_url(resp.url) or "availability" in resp.url.lower():
            record_response_event(
                trace,
                url=resp.url,
                method=resp.request.method,
                status=resp.status,
                matched_calendar=False,
            )
    except Exception:
        if is_calendar_api_url(resp.url):
            record_response_event(
                trace,
                url=resp.url,
                method=getattr(resp.request, "method", "GET"),
                status=getattr(resp, "status", None) or 0,
                matched_calendar=False,
            )
    return False


def _wait_for_listing_markers(page, expected_listing_id: str | None, timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        url = page.url
        if expected_listing_id and extract_listing_id(url) == expected_listing_id:
            return True
        for sel in _CALENDAR_UI_SELECTORS:
            try:
                if page.locator(sel).first.count() > 0:
                    return True
            except Exception:
                pass
        page.wait_for_timeout(250)
    return bool(expected_listing_id and extract_listing_id(page.url) == expected_listing_id)


def _trigger_calendar_ui(page) -> bool:
    triggered = False
    try:
        page.evaluate("window.scrollTo(0, Math.min(1200, document.body.scrollHeight * 0.35))")
        page.wait_for_timeout(400)
    except Exception:
        pass
    for sel in _CALENDAR_UI_SELECTORS[2:]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                loc.click(timeout=2000)
                triggered = True
                page.wait_for_timeout(600)
                break
        except Exception:
            continue
    return triggered


def _collect_page_state(page) -> tuple[str, str, str, str, bytes | None]:
    url = page.url
    title = page.title()
    body = ""
    html = ""
    screenshot = None
    try:
        body = page.inner_text("body")
    except Exception:
        pass
    try:
        html = page.content()[:8000]
    except Exception:
        pass
    try:
        screenshot = page.screenshot(full_page=False)
    except Exception:
        pass
    return url, title, body, html, screenshot


def _wait_for_calendar_capture(page, captured: list[dict], timeout_s: int) -> None:
    for _ in range(max(1, timeout_s)):
        if captured:
            return
        _trigger_calendar_ui(page)
        page.wait_for_timeout(1000)


def _fetch_once(
    *,
    listing_url: str,
    worker: AirbnbWorker,
    proxy: ProxyConfig,
    storage_path: Path,
    timeout_s: int,
    use_storage: bool,
    object_id: str = "",
    attempt: int = 1,
    reload_retry: bool = False,
) -> _AttemptResult:
    base = CalendarCheckResult(
        status=CheckResult.BROWSER_ERROR,
        worker_id=worker.worker_id,
        proxy_id=worker.proxy_id,
        object_id=object_id,
    )
    captured: list[dict] = []
    expected_id = extract_listing_id(listing_url)
    trace = CalendarFetchTrace(
        object_id=object_id,
        worker_id=worker.worker_id,
        requested_url=listing_url,
        listing_id=expected_id,
        attempt=attempt,
        listener_attached_before_navigation=True,
        reload_retry_used=reload_retry,
    )

    from playwright.sync_api import sync_playwright

    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ]
    proxy_cfg = {
        "server": proxy.server,
        "username": proxy.username,
        "password": proxy.password,
    }
    nav_start = time.monotonic()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=args, proxy=proxy_cfg)
        ctx_kwargs: dict = {
            "locale": "en-US",
            "user_agent": worker.user_agent,
            "viewport": {"width": 1366, "height": 900},
        }
        if use_storage and storage_path.exists():
            ctx_kwargs["storage_state"] = str(storage_path)
        ctx = browser.new_context(**ctx_kwargs)
        page = ctx.new_page()

        def on_response(resp):
            _maybe_capture_response(resp, captured, trace)

        page.on("response", on_response)
        try:
            response = page.goto(
                listing_url,
                wait_until="domcontentloaded",
                timeout=timeout_s * 1000,
            )
            trace.domcontentloaded_ms = int((time.monotonic() - nav_start) * 1000)
            if response is not None:
                record_response_event(
                    trace,
                    url=listing_url,
                    method="GET",
                    status=response.status,
                    matched_calendar=False,
                )
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
                trace.networkidle = "ok"
            except Exception:
                trace.networkidle = "timeout"

            _wait_for_listing_markers(page, expected_id, timeout_ms=8000)
            trace.listing_markers_present = _wait_for_listing_markers(
                page, expected_id, timeout_ms=500
            )

            if reload_retry:
                page.reload(wait_until="domcontentloaded", timeout=timeout_s * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass

            _wait_for_calendar_capture(page, captured, timeout_s)
        except Exception as exc:
            msg = str(exc)
            if _PROXY_ERROR.search(msg) or "ERR_FAILED" in msg:
                base.status = (
                    CheckResult.PROXY_ERROR if _PROXY_ERROR.search(msg) else CheckResult.BROWSER_ERROR
                )
            elif "timeout" in msg.lower():
                base.status = CheckResult.TIMEOUT
            base.message = msg[:300]
            url, title, body, html, screenshot = _collect_page_state(page)
            trace.final_url = url
            trace.page_title = title[:200]
            ctx.close()
            browser.close()
            return _AttemptResult(
                base=base,
                captured=captured,
                url=url,
                title=title,
                body=body,
                html=html,
                screenshot=screenshot,
                trace=trace,
            )

        url, title, body, html, screenshot = _collect_page_state(page)
        trace.final_url = url
        trace.page_title = title[:200]
        trace.calendar_ui_present = any(
            sel in body.lower() for sel in ("check-in", "check availability", "book it")
        )
        try:
            ctx.storage_state(path=str(storage_path))
        except Exception:
            pass
        ctx.close()
        browser.close()

    page_kind = classify_page_kind(
        requested_url=listing_url,
        final_url=url,
        title=title,
        body_text=body,
        expected_listing_id=expected_id,
    )
    trace.page_kind = page_kind.value
    return _AttemptResult(
        base=base,
        captured=captured,
        url=url,
        title=title,
        body=body,
        html=html,
        screenshot=screenshot,
        trace=trace,
        page_kind=page_kind,
    )


def _finalize_failure(
    attempt: _AttemptResult,
    *,
    default_message: str,
) -> CalendarCheckResult:
    result = attempt.base
    result.diagnostic = attempt.trace.to_report_dict()
    result.diagnostic["page_kind"] = attempt.page_kind.value

    captcha = detect_page_state(attempt.url, attempt.title, attempt.body)
    if captcha is not None:
        result.status = captcha
        result.message = f"detected on page: {attempt.title[:120]}"
        save_failure_artifacts(
            attempt.trace,
            screenshot_bytes=attempt.screenshot,
            body_text=attempt.body,
            html_snippet=attempt.html,
        )
        return result

    if attempt.captured:
        try:
            result.days = _parse_calendar_payload(attempt.captured)
            if result.days:
                result.status = CheckResult.SUCCESS
                return result
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            result.status = CheckResult.PARSE_ERROR
            result.message = str(exc)[:300]
            save_failure_artifacts(
                attempt.trace,
                screenshot_bytes=attempt.screenshot,
                body_text=attempt.body,
                html_snippet=attempt.html,
            )
            return result

    if attempt.page_kind != PageKind.LISTING_PAGE:
        result.status = page_kind_to_check_result(attempt.page_kind)
        result.message = f"page_state={attempt.page_kind.value}"
        save_failure_artifacts(
            attempt.trace,
            screenshot_bytes=attempt.screenshot,
            body_text=attempt.body,
            html_snippet=attempt.html,
        )
        return result

    if result.status in {CheckResult.TIMEOUT, CheckResult.PROXY_ERROR}:
        save_failure_artifacts(
            attempt.trace,
            screenshot_bytes=attempt.screenshot,
            body_text=attempt.body,
            html_snippet=attempt.html,
        )
        return result

    result.status = CheckResult.PARSE_ERROR
    result.message = default_message
    save_failure_artifacts(
        attempt.trace,
        screenshot_bytes=attempt.screenshot,
        body_text=attempt.body,
        html_snippet=attempt.html,
    )
    return result


def fetch_calendar_with_worker(
    worker: AirbnbWorker,
    proxy: ProxyConfig,
    listing_url: str,
    *,
    object_id: str = "",
    timeout_s: int = 45,
    max_concurrency: int = 1,
    min_delay: float = 0,
    max_delay: float = 0,
) -> CalendarCheckResult:
    listing_url = normalize_listing_url(listing_url)
    base = CalendarCheckResult(
        status=CheckResult.BROWSER_ERROR,
        worker_id=worker.worker_id,
        proxy_id=worker.proxy_id,
        object_id=object_id,
    )
    logger.info(
        "CHECK_STARTED object_id=%s worker_id=%s proxy_id=%s endpoint=%s",
        object_id,
        worker.worker_id,
        worker.proxy_id,
        mask_proxy_server(proxy.server),
    )
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        base.status = CheckResult.BROWSER_ERROR
        base.message = "Playwright not installed"
        return base

    profile_path = Path(worker.profile_path)
    profile_path.mkdir(parents=True, exist_ok=True)
    storage_path = profile_path / "storage_state.json"
    try:
        with worker_execution_slot(
            worker.worker_id,
            max_concurrency=max_concurrency,
            min_delay=min_delay,
            max_delay=max_delay,
        ):
            with profile_directory_lock(profile_path):
                attempt = _fetch_once(
                    listing_url=listing_url,
                    worker=worker,
                    proxy=proxy,
                    storage_path=storage_path,
                    timeout_s=timeout_s,
                    use_storage=True,
                    object_id=object_id,
                    attempt=1,
                )
                if attempt.captured and attempt.page_kind == PageKind.LISTING_PAGE:
                    result = attempt.base
                    result.days = _parse_calendar_payload(attempt.captured)
                    result.status = CheckResult.SUCCESS
                    result.diagnostic = attempt.trace.to_report_dict()
                    logger.info(
                        "CHECK_SUCCESS object_id=%s worker_id=%s days=%s",
                        object_id,
                        worker.worker_id,
                        len(result.days),
                    )
                    return result

                if (
                    not attempt.captured
                    and attempt.page_kind == PageKind.LISTING_PAGE
                    and attempt.base.status not in {
                        CheckResult.TIMEOUT,
                        CheckResult.PROXY_ERROR,
                        CheckResult.BROWSER_ERROR,
                    }
                ):
                    retry = _fetch_once(
                        listing_url=listing_url,
                        worker=worker,
                        proxy=proxy,
                        storage_path=storage_path,
                        timeout_s=timeout_s,
                        use_storage=True,
                        object_id=object_id,
                        attempt=2,
                        reload_retry=True,
                    )
                    if retry.captured:
                        result = retry.base
                        result.days = _parse_calendar_payload(retry.captured)
                        result.status = CheckResult.SUCCESS
                        result.diagnostic = retry.trace.to_report_dict()
                        logger.info(
                            "CHECK_SUCCESS object_id=%s worker_id=%s days=%s retry=reload",
                            object_id,
                            worker.worker_id,
                            len(result.days),
                        )
                        return result
                    attempt = retry

                if not attempt.captured and attempt.page_kind == PageKind.LISTING_PAGE:
                    fresh = _fetch_once(
                        listing_url=listing_url,
                        worker=worker,
                        proxy=proxy,
                        storage_path=storage_path,
                        timeout_s=timeout_s,
                        use_storage=False,
                        object_id=object_id,
                        attempt=3,
                    )
                    if fresh.captured and fresh.page_kind == PageKind.LISTING_PAGE:
                        result = fresh.base
                        result.days = _parse_calendar_payload(fresh.captured)
                        result.status = CheckResult.SUCCESS
                        result.diagnostic = fresh.trace.to_report_dict()
                        return result
                    if fresh.page_kind != PageKind.LISTING_PAGE:
                        attempt = fresh

                result = _finalize_failure(
                    attempt,
                    default_message="listing loaded but calendar API response absent",
                )
                logger.warning(
                    "CHECK_FAILED object_id=%s worker_id=%s result=%s page=%s",
                    object_id,
                    worker.worker_id,
                    result.status.value,
                    attempt.page_kind.value,
                )
                return result
    except Exception as exc:
        msg = str(exc)
        if _PROXY_ERROR.search(msg):
            base.status = CheckResult.PROXY_ERROR
        else:
            base.status = CheckResult.BROWSER_ERROR
        base.message = msg[:300]
        logger.warning(
            "CHECK_FAILED object_id=%s worker_id=%s result=%s",
            object_id,
            worker.worker_id,
            base.status.value,
        )
        return base


def run_calendar_trace(
    worker: AirbnbWorker,
    proxy: ProxyConfig,
    listing_url: str,
    *,
    object_id: str = "",
    timeout_s: int = 45,
) -> CalendarCheckResult:
    """Diagnostic entry: same fetch path, always returns trace in diagnostic."""
    return fetch_calendar_with_worker(
        worker,
        proxy,
        listing_url,
        object_id=object_id,
        timeout_s=timeout_s,
        max_concurrency=1,
        min_delay=0,
        max_delay=0,
    )
