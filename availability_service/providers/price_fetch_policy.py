"""Fail-fast pricing fetch policy (Availability layer; Agent1 formula unchanged)."""
from __future__ import annotations

import logging
import random
import re
import time
from enum import Enum
from typing import Any, Optional

from .pricing_metrics import PriceFetchMetrics

logger = logging.getLogger(__name__)


class PriceAttemptReason(str, Enum):
    SUCCESS = "SUCCESS"
    NO_PRICE = "NO_PRICE"
    TIMEOUT = "TIMEOUT"
    ACCESS_DENIED = "ACCESS_DENIED"
    BROWSER_ERROR = "BROWSER_ERROR"
    OTHER_TRANSIENT_ERROR = "OTHER_TRANSIENT_ERROR"


def classified_fetch_once(parser: Any, url: str, check_in: Any, check_out: Any) -> tuple[Optional[float], PriceAttemptReason]:
    """One page load using Agent1 parser helpers; classifies outcome for retry policy."""
    import config
    from airbnb_url import normalize_airbnb_url, resolve_currency
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
    from datetime import date

    parser._target_currency = resolve_currency(url)
    base = normalize_airbnb_url(url)
    parts = urlparse(base)
    query = parse_qs(parts.query)
    query["check_in"] = [str(check_in)]
    query["check_out"] = [str(check_out)]
    dated_url = urlunparse(parts._replace(query=urlencode(query, doseq=True)))

    try:
        parser._open_listing_url(dated_url)
        parser._close_popup(fast=True)
        price_sleep = max(0.0, float(getattr(config, "PRICE_PAGE_SLEEP_SEC", 0.5)))
        if price_sleep:
            time.sleep(price_sleep + random.uniform(0, 0.5))
        page_source = parser._wait_for_price_page_source(
            poll=float(getattr(config, "PRICE_WAIT_POLL_SEC", 0.25))
        )
    except TimeoutError:
        return None, PriceAttemptReason.TIMEOUT
    except Exception:
        return None, PriceAttemptReason.BROWSER_ERROR

    amount, display = parser._extract_price_from_html(page_source)
    if not amount:
        amount, display = parser._find_price_in_json_text(page_source)
    if not amount:
        parsed = parser._extract_price_from_dom()
        amount = parsed.get("Цена", "")
        display = parsed.get("Цена_отображение", display)

    if not amount:
        diag = parser._diagnose_page(page_source)
        if diag.startswith("Access Denied"):
            return None, PriceAttemptReason.ACCESS_DENIED
        return None, PriceAttemptReason.NO_PRICE

    try:
        value = float(amount)
    except ValueError:
        return None, PriceAttemptReason.OTHER_TRANSIENT_ERROR

    try:
        days = (date.fromisoformat(str(check_out)) - date.fromisoformat(str(check_in))).days + 1
    except ValueError:
        days = None
    if days and days < 27 and re.search(r"помесячно|month", str(display), re.I):
        value = value * days / 30.0
    return value, PriceAttemptReason.SUCCESS


def fetch_price_with_fail_fast(
    parser: Any,
    metrics: PriceFetchMetrics,
    url: str,
    check_in: Any,
    check_out: Any,
) -> Optional[float]:
    """Bounded retries for transient errors; NO_PRICE returns after one page load."""
    import config

    metrics.fetch_calls += 1
    retries = max(1, int(getattr(config, "PRICE_FETCH_RETRIES", 1)))
    max_restarts = max(0, int(getattr(config, "PRICE_BLOCK_RESTARTS", 2)))
    restarts = 0

    for attempt in range(1, retries + 1):
        metrics.page_loads += 1
        value, reason = classified_fetch_once(parser, url, check_in, check_out)
        if reason == PriceAttemptReason.SUCCESS:
            return value
        if reason == PriceAttemptReason.ACCESS_DENIED:
            metrics.price_access_denied_results += 1
            if restarts >= max_restarts:
                logger.warning(
                    f"fetch_price_for_period {check_in}/{check_out}: "
                    f"блок после {restarts} рестартов браузера — сдаёмся"
                )
                metrics.record_fetch_result(None)
                return None
            restarts += 1
            pause = 5 + random.uniform(0, 5)
            logger.warning(
                f"fetch_price_for_period {check_in}/{check_out}: Access Denied — "
                f"рестарт браузера {restarts}/{max_restarts} через {pause:.1f}s"
            )
            parser.close()
            time.sleep(pause)
            continue
        if reason == PriceAttemptReason.NO_PRICE:
            metrics.price_no_price_results += 1
            metrics.record_fetch_result(None)
            return None
        if reason == PriceAttemptReason.TIMEOUT:
            metrics.price_timeout_results += 1
        elif reason == PriceAttemptReason.BROWSER_ERROR:
            metrics.price_browser_error_results += 1
        else:
            metrics.price_other_transient_results += 1
        if attempt < retries:
            pause = float(getattr(config, "PRICE_RETRY_SLEEP_SEC", 6))
            pause += random.uniform(0, min(3.0, pause * 0.4))
            logger.info(
                f"fetch_price_for_period retry {attempt}/{retries} "
                f"{check_in}/{check_out} через {pause:.1f}s ({reason.value})"
            )
            time.sleep(pause)
    metrics.record_fetch_result(None)
    return None
