"""Fail-fast NO_PRICE policy, wall-clock cap, and level-1/level-2 semantics."""
from __future__ import annotations

from datetime import date
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from availability_service.app.models import MonthWindowItem
from availability_service.providers.airbnb_adapter import collect_prices_for_window
from availability_service.providers.price_fetch_policy import (
    PriceAttemptReason,
    classified_fetch_once,
    fetch_price_with_fail_fast,
)
from availability_service.providers.pricing_metrics import PriceFetchMetrics


def _fake_config():
    fake_config = ModuleType("config")
    fake_config.PRICE_FETCH_RETRIES = 3
    fake_config.PRICE_BLOCK_RESTARTS = 0
    fake_config.PRICE_RETRY_SLEEP_SEC = 0
    fake_config.PRICE_PAGE_SLEEP_SEC = 0
    fake_config.PRICE_WAIT_POLL_SEC = 0
    return fake_config


def _fake_airbnb_url():
    fake_airbnb_url = ModuleType("airbnb_url")
    fake_airbnb_url.normalize_airbnb_url = lambda u: u
    fake_airbnb_url.resolve_currency = lambda u: "THB"
    return fake_airbnb_url


class FakeParser:
    def __init__(self):
        self.sb = object()
        self.open_calls = 0
        self.close_calls = 0
        self._target_currency = "THB"
        self._fail_open = False
        self._fail_timeout = False
        self._blocked = False
        self._price = None

    def _open_listing_url(self, url):
        self.open_calls += 1
        if self._fail_timeout:
            raise TimeoutError("hang")
        if self._fail_open:
            raise RuntimeError("browser down")

    def _close_popup(self, fast=True):
        pass

    def _wait_for_price_page_source(self, poll=0.25):
        return "<html></html>"

    def _extract_price_from_html(self, page_source):
        return ("", "")

    def _find_price_in_json_text(self, page_source):
        return ("", "")

    def _extract_price_from_dom(self):
        return {"Цена": self._price or "", "Цена_отображение": ""}

    def _diagnose_page(self, page_source):
        if self._blocked:
            return "Access Denied"
        return "цены нет"

    def close(self):
        self.close_calls += 1


def test_no_price_single_page_load_no_internal_retry():
    parser = FakeParser()
    metrics = PriceFetchMetrics()
    fake_config = _fake_config()
    fake_airbnb_url = ModuleType("airbnb_url")
    fake_airbnb_url.normalize_airbnb_url = lambda u: u
    fake_airbnb_url.resolve_currency = lambda u: "THB"

    with patch.dict(
        "sys.modules",
        {"config": fake_config, "airbnb_url": fake_airbnb_url},
    ):
        result = fetch_price_with_fail_fast(
            parser, metrics, "http://airbnb/1", "2026-09-01", "2026-09-30"
        )
    assert result is None
    assert parser.open_calls == 1
    assert metrics.fetch_calls == 1
    assert metrics.page_loads == 1
    assert metrics.internal_retries == 0
    assert metrics.price_no_price_results == 1


def test_timeout_retries_then_no_price():
    parser = FakeParser()
    parser._fail_timeout = True
    metrics = PriceFetchMetrics()

    with patch.dict(
        "sys.modules",
        {"config": _fake_config(), "airbnb_url": _fake_airbnb_url()},
    ):
        result = fetch_price_with_fail_fast(
            parser, metrics, "http://airbnb/1", "2026-09-01", "2026-09-30"
        )
    assert result is None
    assert parser.open_calls == 3
    assert metrics.page_loads == 3
    assert metrics.fetch_calls == 1
    assert metrics.internal_retries == 2
    assert metrics.price_timeout_results == 3


def test_access_denied_restart_policy():
    parser = FakeParser()
    parser._blocked = True
    metrics = PriceFetchMetrics()
    fake_config = _fake_config()
    fake_config.PRICE_BLOCK_RESTARTS = 2

    with patch.dict(
        "sys.modules",
        {"config": fake_config, "airbnb_url": _fake_airbnb_url()},
    ):
        result = fetch_price_with_fail_fast(
            parser, metrics, "http://airbnb/1", "2026-09-01", "2026-09-30"
        )
    assert result is None
    assert parser.close_calls == 2
    assert metrics.price_access_denied_results == 3
    assert metrics.fetch_calls == 1


def test_level1_no_price_allows_level2():
    calls: list[tuple[date, date]] = []

    def fetch(check_in: date, check_out: date):
        calls.append((check_in, check_out))
        if len(calls) == 1:
            return None
        return 100000.0

    window = [MonthWindowItem(2026, 9, "2026-09", "Sep 26")]
    availability = {date(2026, 9, d): True for d in range(1, 31)}

    entries = collect_prices_for_window(
        fetch,
        availability,
        window,
        min_segment_days=5,
    )
    assert len(calls) == 2
    assert entries["2026-09"]["price"] is not None


def test_level2_no_price_insufficient_data():
    def fetch(check_in: date, check_out: date):
        return None

    window = [MonthWindowItem(2026, 9, "2026-09", "Sep 26")]
    availability = {date(2026, 9, d): True for d in range(1, 31)}
    availability[date(2026, 9, 10)] = False

    entries = collect_prices_for_window(
        fetch,
        availability,
        window,
        min_segment_days=5,
    )
    assert entries["2026-09"]["status"] == "insufficient_data"
    assert entries["2026-09"]["price"] is None


def test_wall_clock_cap_preserves_calendar_semantics():
    metrics = PriceFetchMetrics()
    slow_calls = 0

    def slow_fetch(check_in: date, check_out: date):
        nonlocal slow_calls
        slow_calls += 1
        return 50000.0

    window = [
        MonthWindowItem(2026, 10, "2026-10", "Oct 26"),
        MonthWindowItem(2026, 11, "2026-11", "Nov 26"),
    ]

    with patch("availability_service.providers.airbnb_adapter.time.perf_counter") as perf:
        perf.side_effect = [0.0, 0.0, 10.0]
        entries = collect_prices_for_window(
            slow_fetch,
            {date(2026, 10, d): True for d in range(1, 32)},
            window,
            metrics=metrics,
            price_object_max_seconds=10,
        )

    assert slow_calls == 1
    assert metrics.price_object_wallclock_capped is True
    assert entries["2026-10"]["price"] == 50000.0
    assert entries["2026-11"]["status"] == "insufficient_data"
    assert entries["2026-11"]["price"] is None


def test_classified_fetch_once_success():
    parser = FakeParser()
    parser._price = "100000"

    with patch.dict(
        "sys.modules",
        {"config": _fake_config(), "airbnb_url": _fake_airbnb_url()},
    ):
        value, reason = classified_fetch_once(parser, "http://airbnb/1", "2026-09-01", "2026-09-30")
    assert reason == PriceAttemptReason.SUCCESS
    assert value == 100000.0
