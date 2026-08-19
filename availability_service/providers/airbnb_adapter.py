"""Minimal bridge to Agent1 Airbnb modules (no workflow / handoff / listing parse).

Imports Agent1 code only when live fetch is invoked. Agent1 files are not modified.
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from ..app.config import PROJECT_ROOT
from ..app.models import MonthWindowItem
from .price_fetch_policy import fetch_price_with_fail_fast
from .pricing_metrics import PriceFetchMetrics

AGENT1_CANDIDATES = [
    Path("agent_1_parser/airbnb_scraper/Agent-real-estate-1"),
    Path("agent_1_parser/airbnb_parser"),
]


FetchPrice = Callable[[date, date], Optional[float]]
PriceCleanup = Callable[[], None]
PriceMetricsGetter = Callable[[], PriceFetchMetrics]


def resolve_agent1_root() -> Path | None:
    for rel in AGENT1_CANDIDATES:
        root = PROJECT_ROOT / rel
        if (root / "monthly_pricing.py").exists() and (root / "availability.py").exists():
            return root
    return None


def _ensure_agent1_importable() -> Path:
    root = resolve_agent1_root()
    if root is None:
        raise RuntimeError(
            f"Agent1 Airbnb modules not found under {', '.join(str(p) for p in AGENT1_CANDIDATES)}"
        )
    path = str(root)
    if path not in sys.path:
        sys.path.insert(0, path)
    return root


def _calendar_fetch_impl(source_url: str, timeout_s: int) -> dict[date, bool]:
    _ensure_agent1_importable()
    from availability import fetch_calendar_days  # type: ignore[import-not-found]

    return fetch_calendar_days(source_url, timeout_s=timeout_s)


def fetch_calendar_days_live(source_url: str, timeout_s: int = 45) -> dict[date, bool]:
    """Playwright intercept of PdpAvailabilityCalendar (Agent1 availability.py)."""
    from ..app.server_concurrency import (
        calendar_fetch_slot,
        current_batch_context,
        is_server_concurrency_configured,
    )

    ctx = current_batch_context()
    object_id = ctx.object_id if ctx else "calendar"
    metrics = ctx.metrics if ctx else None
    if is_server_concurrency_configured():
        with calendar_fetch_slot(object_id, metrics=metrics):
            return _calendar_fetch_impl(source_url, timeout_s)
    return _calendar_fetch_impl(source_url, timeout_s)


def _metrics_airbnb_parser_class() -> type:
    _ensure_agent1_importable()
    from airbnb_parser import AirbnbParser  # type: ignore[import-not-found]

    class MetricsAirbnbParser(AirbnbParser):
        def __init__(self, headless: bool = True, metrics: PriceFetchMetrics | None = None):
            super().__init__(headless=headless)
            self._price_metrics = metrics or PriceFetchMetrics()

        @property
        def price_metrics(self) -> PriceFetchMetrics:
            return self._price_metrics

        def _init_sb(self):
            self._price_metrics.browser_inits += 1
            return super()._init_sb()

        def fetch_price_for_period(self, url, check_in, check_out):
            return fetch_price_with_fail_fast(
                self, self._price_metrics, url, check_in, check_out
            )

    return MetricsAirbnbParser


def create_selenium_price_fetcher(
    source_url: str,
    *,
    headless: bool = True,
) -> tuple[FetchPrice, PriceCleanup, PriceMetricsGetter]:
    """Minimal Selenium bridge: only fetch_price_for_period, not full listing parse."""
    MetricsAirbnbParser = _metrics_airbnb_parser_class()
    metrics = PriceFetchMetrics()
    parser = MetricsAirbnbParser(headless=headless, metrics=metrics)

    def fetch(check_in: date, check_out: date) -> Optional[float]:
        return parser.fetch_price_for_period(source_url, check_in, check_out)

    def cleanup() -> None:
        try:
            parser.close()
        except Exception:
            pass

    def get_metrics() -> PriceFetchMetrics:
        return metrics

    return fetch, cleanup, get_metrics


def _capped_month_entry(note: str = "pricing wall-clock cap reached") -> dict:
    return {
        "price": None,
        "status": "insufficient_data",
        "note": note,
    }


def collect_prices_for_window(
    fetch_price: FetchPrice,
    availability: dict[date, bool],
    window: list[MonthWindowItem],
    *,
    min_segment_days: int = 5,
    metrics: PriceFetchMetrics | None = None,
    price_object_max_seconds: float = 0,
) -> dict[str, dict]:
    """Price entries keyed by YYYY-MM for the exact rolling window (not Agent1 iter_months_ahead)."""
    _ensure_agent1_importable()
    from monthly_pricing import price_for_month  # type: ignore[import-not-found]

    if metrics is not None:
        metrics.months_requested = len(window)

    result: dict[str, dict] = {}
    pricing_start = time.perf_counter()
    cap = max(0.0, float(price_object_max_seconds))

    for item in window:
        if cap > 0 and metrics is not None:
            elapsed = time.perf_counter() - pricing_start
            if elapsed >= cap:
                metrics.price_object_wallclock_capped = True
                result[item.key] = _capped_month_entry()
                continue

        entry = price_for_month(
            fetch_price,
            availability,
            item.year,
            item.month,
            min_segment_days=min_segment_days,
        )
        result[item.key] = entry

    if metrics is not None:
        metrics.record_month_entries(result)
    return result
