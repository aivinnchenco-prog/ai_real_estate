"""Worker-bound Selenium pricing fetch (Phase 2)."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Generator, Optional

from ..models import MonthWindowItem
from ...providers.airbnb_adapter import _metrics_airbnb_parser_class, collect_prices_for_window
from ...providers.price_fetch_policy import fetch_price_with_fail_fast
from ...providers.pricing_metrics import PriceFetchMetrics
from .calendar_fetch import profile_directory_lock, worker_execution_slot
from .config import mask_proxy_server
from .models import AirbnbWorker, PricingCheckResult, PricingResult, ProxyConfig
from .page_state import PageKind, classify_page_kind, extract_listing_id
from .worker_paths import pricing_profile_path
from .worker_urls import normalize_airbnb_pricing_url

logger = logging.getLogger(__name__)

_PROXY_ERROR = re.compile(
    r"proxy|tunnel|407|authentication required|net::err_proxy|connection refused",
    re.I,
)
_SECRET_RE = re.compile(
    r"(password|authorization|cookie|set-cookie|proxy|credential|token)",
    re.I,
)


def selenium_proxy_string(proxy: ProxyConfig) -> str:
    """SeleniumBase format user:pass@host:port — never log return value."""
    server = proxy.server.strip()
    if server.startswith("http://"):
        server = server[7:]
    elif server.startswith("https://"):
        server = server[8:]
    return f"{proxy.username}:{proxy.password}@{server}"


def _page_kind_to_pricing_result(kind: PageKind) -> PricingResult:
    mapping = {
        PageKind.CAPTCHA: PricingResult.CAPTCHA,
        PageKind.CHALLENGE: PricingResult.CHALLENGE,
        PageKind.GENERIC_HOMEPAGE: PricingResult.INVALID_LISTING,
        PageKind.NOT_FOUND: PricingResult.INVALID_LISTING,
        PageKind.LISTING_REMOVED: PricingResult.INVALID_LISTING,
        PageKind.LISTING_REDIRECT: PricingResult.INVALID_LISTING,
        PageKind.LOGIN_PAGE: PricingResult.INVALID_LISTING,
    }
    return mapping.get(kind, PricingResult.PARSE_ERROR)


def _sanitize_error(msg: str) -> str:
    out = msg
    for pat in (r"password=\S+", r"Bearer \S+", r"Basic \S+"):
        out = re.sub(pat, "[redacted]", out, flags=re.I)
    return out[:300]


def _patch_parser_for_worker(
    parser,
    *,
    worker: AirbnbWorker,
    proxy: ProxyConfig,
    profile_path: Path,
    pricing_url_normalize,
) -> None:
    normalize_fn = pricing_url_normalize

    def _worker_init_sb():
        from seleniumbase import sb_cdp

        parser._price_metrics.browser_inits += 1
        profile_path.mkdir(parents=True, exist_ok=True)
        proxy_str = selenium_proxy_string(proxy)
        logger.info(
            "PRICING_BROWSER_INIT worker_id=%s proxy_id=%s profile=%s",
            worker.worker_id,
            worker.proxy_id,
            profile_path.name,
        )
        parser.sb = sb_cdp.Chrome(
            url="about:blank",
            lang="en",
            headless=parser.HEADLESS_MODE,
            agent=worker.user_agent,
            user_data_dir=str(profile_path),
            proxy=proxy_str,
        )
        parser.sb.maximize()
        return parser.sb

    parser._init_sb = _worker_init_sb

    def _worker_fetch_price(url, check_in, check_out):
        return fetch_price_with_fail_fast(
            parser,
            parser._price_metrics,
            url,
            check_in,
            check_out,
            normalize_url=normalize_fn,
        )

    parser.fetch_price_for_period = _worker_fetch_price


def build_worker_parser(
    worker: AirbnbWorker,
    proxy: ProxyConfig,
    *,
    headless: bool = True,
    metrics: PriceFetchMetrics | None = None,
) -> object:
    MetricsAirbnbParser = _metrics_airbnb_parser_class()
    parser = MetricsAirbnbParser(headless=headless, metrics=metrics or PriceFetchMetrics())
    _patch_parser_for_worker(
        parser,
        worker=worker,
        proxy=proxy,
        profile_path=pricing_profile_path(worker),
        pricing_url_normalize=normalize_airbnb_pricing_url,
    )
    return parser


def probe_pricing_listing(parser, listing_url: str) -> PricingResult | None:
    """Pre-check listing page before month pricing loop."""
    import time

    import config
    from airbnb_url import normalize_airbnb_url

    try:
        parser._open_listing_url(normalize_airbnb_url(listing_url))
        parser._close_popup(fast=True)
        sleep_sec = max(0.0, float(getattr(config, "PRICE_PAGE_SLEEP_SEC", 0.5)))
        if sleep_sec:
            time.sleep(sleep_sec + 1.5)
    except TimeoutError:
        return PricingResult.TIMEOUT
    except Exception as exc:
        if _PROXY_ERROR.search(str(exc)):
            return PricingResult.PROXY_ERROR
        return PricingResult.BROWSER_ERROR

    final_url = ""
    title = ""
    body = ""
    try:
        sb = parser._ensure_sb()
        final_url = sb.get_current_url()
        title = sb.get_title()
        body = sb.get_page_source()[:12000]
    except Exception:
        pass

    kind = classify_page_kind(
        requested_url=listing_url,
        final_url=final_url,
        title=title,
        body_text=body,
        expected_listing_id=extract_listing_id(listing_url),
    )
    if kind == PageKind.LISTING_PAGE:
        return None
    if kind in {PageKind.CAPTCHA, PageKind.CHALLENGE}:
        return _page_kind_to_pricing_result(kind)
    if kind in {
        PageKind.GENERIC_HOMEPAGE,
        PageKind.NOT_FOUND,
        PageKind.LISTING_REMOVED,
    }:
        return _page_kind_to_pricing_result(kind)
    return None


def _session_status_from_metrics(metrics: PriceFetchMetrics) -> PricingResult:
    if metrics.price_access_denied_results > 0 and metrics.successful_months == 0:
        return PricingResult.CAPTCHA
    if metrics.price_timeout_results > 0 and metrics.successful_months == 0:
        if metrics.fetch_calls == metrics.price_timeout_results:
            return PricingResult.TIMEOUT
    if metrics.price_browser_error_results > 0 and metrics.successful_months == 0:
        if metrics.fetch_calls == metrics.price_browser_error_results:
            return PricingResult.BROWSER_ERROR
    return PricingResult.SUCCESS


@contextmanager
def worker_pricing_session(
    worker: AirbnbWorker,
    proxy: ProxyConfig,
    *,
    max_concurrency: int = 1,
    min_delay: float = 0,
    max_delay: float = 0,
    acquire_slot: bool = True,
    headless: bool = True,
) -> Generator[object, None, None]:
    profile = pricing_profile_path(worker)
    parser = build_worker_parser(worker, proxy, headless=headless)

    @contextmanager
    def _inner() -> Generator[object, None, None]:
        with profile_directory_lock(profile):
            try:
                yield parser
            finally:
                try:
                    parser.close()
                except Exception:
                    pass

    if acquire_slot:
        with worker_execution_slot(
            worker.worker_id,
            max_concurrency=max_concurrency,
            min_delay=min_delay,
            max_delay=max_delay,
        ):
            with _inner():
                yield parser
    else:
        with _inner():
            yield parser


def fetch_pricing_with_worker(
    worker: AirbnbWorker,
    proxy: ProxyConfig,
    listing_url: str,
    availability: dict[date, bool],
    window: list[MonthWindowItem],
    *,
    object_id: str = "",
    min_segment_days: int = 5,
    price_object_max_seconds: float = 0,
    max_concurrency: int = 1,
    min_delay: float = 0,
    max_delay: float = 0,
    acquire_slot: bool = True,
    headless: bool = True,
) -> PricingCheckResult:
    base = PricingCheckResult(
        status=PricingResult.BROWSER_ERROR,
        worker_id=worker.worker_id,
        proxy_id=worker.proxy_id,
        object_id=object_id,
    )
    logger.info(
        "PRICING_STARTED object_id=%s worker_id=%s proxy_id=%s endpoint=%s ua_prefix=%s",
        object_id,
        worker.worker_id,
        worker.proxy_id,
        mask_proxy_server(proxy.server),
        worker.user_agent[:48],
    )
    try:
        with worker_pricing_session(
            worker,
            proxy,
            max_concurrency=max_concurrency,
            min_delay=min_delay,
            max_delay=max_delay,
            acquire_slot=acquire_slot,
            headless=headless,
        ) as parser:
            invalid = probe_pricing_listing(parser, listing_url)
            if invalid is not None:
                base.status = invalid
                base.message = f"listing probe: {invalid.value}"
                logger.warning(
                    "PRICING_INVALID_LISTING object_id=%s worker_id=%s",
                    object_id,
                    worker.worker_id,
                )
                return base

            metrics: PriceFetchMetrics = parser.price_metrics
            metrics.months_requested = len(window)

            def fetch_price(check_in: date, check_out: date) -> Optional[float]:
                return parser.fetch_price_for_period(listing_url, check_in, check_out)

            entries = collect_prices_for_window(
                fetch_price,
                availability,
                window,
                min_segment_days=min_segment_days,
                metrics=metrics,
                price_object_max_seconds=price_object_max_seconds,
            )
            base.price_entries = entries
            base.status = _session_status_from_metrics(metrics)
            if base.success:
                logger.info(
                    "PRICING_SUCCESS object_id=%s worker_id=%s months_priced=%s",
                    object_id,
                    worker.worker_id,
                    metrics.successful_months,
                )
            else:
                base.message = (
                    f"denied={metrics.price_access_denied_results} "
                    f"browser_err={metrics.price_browser_error_results}"
                )
                logger.warning(
                    "PRICING_%s object_id=%s worker_id=%s",
                    base.status.value,
                    object_id,
                    worker.worker_id,
                )
            return base
    except Exception as exc:
        msg = _sanitize_error(str(exc))
        if _SECRET_RE.search(str(exc)):
            msg = "[redacted error]"
        if _PROXY_ERROR.search(str(exc)):
            base.status = PricingResult.PROXY_ERROR
        else:
            base.status = PricingResult.BROWSER_ERROR
        base.message = msg
        logger.warning(
            "PRICING_%s object_id=%s worker_id=%s",
            base.status.value,
            object_id,
            worker.worker_id,
        )
        return base
