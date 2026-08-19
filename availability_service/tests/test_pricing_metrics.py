"""Tests for pricing fetch metrics (no live Airbnb)."""
from __future__ import annotations

from types import ModuleType
from unittest.mock import patch

from availability_service.providers.pricing_metrics import PriceFetchMetrics
from availability_service.providers.price_fetch_policy import fetch_price_with_fail_fast


def test_internal_retries_from_page_loads_minus_fetch_calls():
    metrics = PriceFetchMetrics(
        fetch_calls=23,
        page_loads=69,
        failed_fetch_calls=23,
        months_requested=12,
    )
    assert metrics.internal_retries == 46
    assert metrics.attempts_total == 23


def test_record_month_entries_counts_prices():
    metrics = PriceFetchMetrics()
    metrics.record_month_entries(
        {
            "2026-09": {"price": 100000},
            "2026-10": {"price": None},
            "2026-11": {"status": "insufficient_data"},
        }
    )
    assert metrics.successful_months == 1


def test_fail_fast_no_price_single_page_load():
    fake_config = ModuleType("config")
    fake_config.PRICE_FETCH_RETRIES = 3
    fake_config.PRICE_BLOCK_RESTARTS = 0
    fake_config.PRICE_RETRY_SLEEP_SEC = 0
    fake_config.PRICE_PAGE_SLEEP_SEC = 0
    fake_config.PRICE_WAIT_POLL_SEC = 0
    fake_airbnb_url = ModuleType("airbnb_url")
    fake_airbnb_url.normalize_airbnb_url = lambda u: u
    fake_airbnb_url.resolve_currency = lambda u: "THB"

    class Parser:
        sb = object()
        open_calls = 0
        _target_currency = "THB"
        _blocked = False
        _price = None

        def _open_listing_url(self, url):
            self.open_calls += 1

        def _close_popup(self, fast=True):
            pass

        def _wait_for_price_page_source(self, poll=0.25):
            return "<html></html>"

        def _extract_price_from_html(self, page_source):
            return ("", "")

        def _find_price_in_json_text(self, page_source):
            return ("", "")

        def _extract_price_from_dom(self):
            return {"Цена": "", "Цена_отображение": ""}

        def _diagnose_page(self, page_source):
            return "цены нет"

        def close(self):
            pass

    parser = Parser()
    metrics = PriceFetchMetrics()
    with patch.dict(
        "sys.modules",
        {"config": fake_config, "airbnb_url": fake_airbnb_url},
    ):
        result = fetch_price_with_fail_fast(
            parser, metrics, "http://airbnb/1", "2026-09-01", "2026-09-30"
        )
    assert result is None
    assert metrics.page_loads == 1
    assert metrics.internal_retries == 0
    assert metrics.price_no_price_results == 1
