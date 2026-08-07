"""Offline regression tests: primary month + background pricing queue."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from monthly_pricing import (
    background_month_keys,
    entry_has_price,
    first_upcoming_month_key,
    month_keys_ahead,
    primary_month_key,
)
from price_cache import load_quote_cache, quote_cache_key, remember_quote, save_price_cache
from pricing_config import background_workers, months_ahead
from pricing_orchestrator import collect_primary_month, schedule_background_pricing
from pricing_queue import PricingQueue
from pricing_state import compute_pricing_status, pricing_months_collected, serialize_calendar
from pricing_worker import process_next_job_for_tests, process_one_job


def days_range(start: date, end: date, available: bool = True) -> dict[date, bool]:
    out = {}
    d = start
    while d <= end:
        out[d] = available
        d += timedelta(days=1)
    return out


class MockParser:
    def __init__(self, prices: dict[tuple[str, str], float | None] | None = None, blocked=False):
        self._prices = prices or {}
        self._blocked = blocked
        self.calls = []

    def _fetch_price_for_period_once(self, url, check_in, check_out):
        self.calls.append((check_in, check_out))
        if self._blocked:
            return None, True
        key = (str(check_in), str(check_out))
        return self._prices.get(key), False


class PricingTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.queue_path = Path(self._tmpdir.name) / "pricing_queue.json"
        self.cache_dir = Path(self._tmpdir.name) / "cache"
        self._old = {
            "PRICE_QUEUE_PATH": config.PRICE_QUEUE_PATH,
            "PRICE_CACHE_DIR": config.PRICE_CACHE_DIR,
            "PRICE_CACHE_ENABLED": config.PRICE_CACHE_ENABLED,
            "PRICE_COLLECT_ENABLED": config.PRICE_COLLECT_ENABLED,
            "PRICE_NOTION_SYNC_ENABLED": config.PRICE_NOTION_SYNC_ENABLED,
            "PRICE_MONTHS_AHEAD": config.PRICE_MONTHS_AHEAD,
        }
        config.PRICE_QUEUE_PATH = str(self.queue_path)
        config.PRICE_CACHE_DIR = str(self.cache_dir)
        config.PRICE_CACHE_ENABLED = True
        config.PRICE_COLLECT_ENABLED = True
        config.PRICE_NOTION_SYNC_ENABLED = False
        config.PRICE_MONTHS_AHEAD = 12
        self.today = date(2026, 7, 16)
        self.url = "https://www.airbnb.com/rooms/999"

    def tearDown(self):
        for k, v in self._old.items():
            setattr(config, k, v)
        self._tmpdir.cleanup()

    def queue(self) -> PricingQueue:
        return PricingQueue(self.queue_path)


class TestPrimaryMonth(PricingTestBase):
    @mock.patch("pricing_orchestrator.fetch_calendar_days")
    def test_primary_collected_first(self, mock_cal):
        mock_cal.return_value = days_range(date(2026, 8, 1), date(2027, 7, 31))
        parser = MockParser({("2026-08-01", "2026-08-31"): 88000.0})
        timings = {}
        prices, _ = collect_primary_month(
            parser, self.url, {}, timings, today=self.today
        )
        primary = primary_month_key(self.today, 12)
        self.assertEqual(primary, "2026-08")
        self.assertTrue(entry_has_price(prices.get(primary)))
        self.assertEqual(prices[primary].get("queue_status"), "priority")
        self.assertLessEqual(len(parser.calls), 2)

    @mock.patch("pricing_orchestrator.fetch_calendar_days")
    def test_primary_available_for_agent3(self, mock_cal):
        mock_cal.return_value = days_range(date(2026, 8, 1), date(2027, 7, 31))
        parser = MockParser({("2026-08-01", "2026-08-31"): 90000.0})
        prices, _ = collect_primary_month(parser, self.url, {}, {}, today=self.today)
        key = first_upcoming_month_key(prices, self.today)
        self.assertEqual(key, "2026-08")

    @mock.patch("pricing_orchestrator.fetch_calendar_days")
    def test_background_not_blocking_primary_path(self, mock_cal):
        mock_cal.return_value = days_range(date(2026, 8, 1), date(2027, 7, 31))
        parser = MockParser({("2026-08-01", "2026-08-31"): 90000.0})
        prices, avail = collect_primary_month(parser, self.url, {}, {}, today=self.today)
        n = schedule_background_pricing(
            object_id="A_20260716_001",
            listing_url=self.url,
            monthly_prices=prices,
            calendar=avail,
        )
        self.assertEqual(n, 11)
        self.assertEqual(len(parser.calls), 1)

    @mock.patch("pricing_orchestrator.fetch_calendar_days")
    def test_primary_failure_does_not_enqueue_all_parallel(self, mock_cal):
        mock_cal.return_value = {}
        parser = MockParser(blocked=True)
        prices, avail = collect_primary_month(parser, self.url, {}, {}, today=self.today)
        n = schedule_background_pricing(
            object_id="A_20260716_002",
            listing_url=self.url,
            monthly_prices=prices,
            calendar=avail,
        )
        self.assertEqual(n, 11)
        q = self.queue()
        self.assertEqual(len(q.jobs_for_object("A_20260716_002")), 11)


class TestQueue(PricingTestBase):
    def test_creates_eleven_background_jobs(self):
        months = background_month_keys(self.today, 12)
        self.assertEqual(len(months), 11)
        q = self.queue()
        q.enqueue_background_months(
            "A_1", self.url, months, existing={}
        )
        q.save()
        self.assertEqual(len(q.jobs_for_object("A_1")), 11)

    def test_month_job_independent(self):
        q = self.queue()
        months = background_month_keys(self.today, 12)
        q.enqueue_background_months("A_1", self.url, months[:3])
        avail = serialize_calendar(days_range(date(2026, 8, 1), date(2026, 10, 31)))
        q.upsert_object("A_1", listing_url=self.url, calendar=avail)
        q.save()

        parser = MockParser({
            ("2026-09-01", "2026-09-30"): 70000.0,
            ("2026-10-01", "2026-10-31"): None,
        })
        while True:
            job = q.pick_next_job()
            if not job:
                break
            q.mark_running(job)
            process_one_job(job, parser=parser, queue=q)
            q.load()
        obj = q.get_object("A_1")
        self.assertTrue(entry_has_price(obj["monthly_prices"].get("2026-09")))

    def test_successful_month_done(self):
        q = self.queue()
        q.enqueue_background_months("A_1", self.url, ["2026-09"])
        q.upsert_object("A_1", listing_url=self.url, calendar=serialize_calendar(
            days_range(date(2026, 9, 1), date(2026, 9, 30))
        ))
        q.save()
        job = q.pick_next_job()
        parser = MockParser({("2026-09-01", "2026-09-30"): 50000.0})
        process_one_job(job, parser=parser, queue=q)
        q.load()
        self.assertEqual(q.jobs_for_object("A_1")[0]["status"], "done")

    def test_failed_month_retry(self):
        q = self.queue()
        q.enqueue_background_months("A_1", self.url, ["2026-09"])
        q.upsert_object("A_1", listing_url=self.url, calendar={})
        q.save()
        job = q.pick_next_job()
        parser = MockParser({})
        process_one_job(job, parser=parser, queue=q)
        q.load()
        self.assertIn(q.jobs_for_object("A_1")[0]["status"], ("retry", "insufficient_data"))

    def test_done_month_not_repeated(self):
        q = self.queue()
        existing = {"2026-09": {"price": 60000, "status": "monthly"}}
        created = q.enqueue_background_months(
            "A_1", self.url, ["2026-09", "2026-10"], existing=existing
        )
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["month"], "2026-10")

    def test_restart_preserves_progress(self):
        q = self.queue()
        q.upsert_object(
            "A_1",
            listing_url=self.url,
            monthly_prices={"2026-08": {"price": 80000, "status": "monthly"}},
        )
        q.enqueue_background_months("A_1", self.url, ["2026-09"])
        q.mark_done(q.jobs_for_object("A_1")[0])
        q.save()
        q2 = PricingQueue(self.queue_path)
        obj = q2.get_object("A_1")
        self.assertTrue(entry_has_price(obj["monthly_prices"]["2026-08"]))


class TestFairness(PricingTestBase):
    def test_round_robin_three_objects(self):
        q = self.queue()
        months = background_month_keys(self.today, 12)
        for oid in ("A", "B", "C"):
            q.enqueue_background_months(oid, self.url, months[:2])
        order = []
        for _ in range(6):
            job = q.pick_next_job()
            if not job:
                break
            order.append((job["object_id"], job["month_depth"]))
            obj = q.get_object(job["object_id"])
            obj["last_served_at"] = time.time()
            q.mark_done(job)
        self.assertEqual(
            [o for o, _ in order],
            ["A", "B", "C", "A", "B", "C"],
        )

    def test_five_objects_no_burst(self):
        q = self.queue()
        for i in range(5):
            q.enqueue_background_months(f"O{i}", self.url, ["2026-09"])
        self.assertEqual(len(q._data["jobs"]), 5)
        self.assertEqual(background_workers(), 1)

    def test_default_concurrency_one(self):
        self.assertEqual(background_workers(), 1)

    def test_problematic_object_does_not_block(self):
        q = self.queue()
        q.enqueue_background_months("BAD", self.url, ["2026-09"])
        q.enqueue_background_months("GOOD", self.url, ["2026-09"])
        bad = [j for j in q._data["jobs"] if j["object_id"] == "BAD"][0]
        bad["status"] = "blocked"
        bad["next_retry_at"] = time.time() + 3600
        job = q.pick_next_job()
        self.assertEqual(job["object_id"], "GOOD")


class TestRetryBackoff(PricingTestBase):
    def test_blocked_not_immediate_retry(self):
        q = self.queue()
        q.enqueue_background_months("A_1", self.url, ["2026-09"])
        q.upsert_object("A_1", listing_url=self.url, calendar={})
        job = q.pick_next_job()
        parser = MockParser(blocked=True)
        process_one_job(job, parser=parser, queue=q)
        q.load()
        updated = q.jobs_for_object("A_1")[0]
        self.assertEqual(updated["status"], "blocked")
        self.assertGreater(updated["next_retry_at"], time.time())

    def test_refill_only_retry_months(self):
        q = self.queue()
        q.enqueue_background_months("A_1", self.url, ["2026-09", "2026-10"])
        for job in q.jobs_for_object("A_1"):
            if job["month"] == "2026-09":
                q.mark_done(job)
        q.save()
        eligible = q._eligible_jobs()
        self.assertEqual(len(eligible), 1)
        self.assertEqual(eligible[0]["month"], "2026-10")


class TestPartialState(PricingTestBase):
    def test_collecting_status(self):
        st = compute_pricing_status({}, months_target=12, has_active_jobs=True)
        self.assertEqual(st, "collecting")

    def test_partial_status(self):
        monthly = {"2026-08": {"price": 80000, "status": "monthly"}}
        st = compute_pricing_status(monthly, months_target=12, has_active_jobs=True)
        self.assertEqual(st, "partial")

    def test_complete_status(self):
        monthly = {k: {"price": 50000, "status": "monthly"} for k in month_keys_ahead(self.today, 12)}
        st = compute_pricing_status(monthly, months_target=12, has_active_jobs=False)
        self.assertEqual(st, "complete")

    def test_counters(self):
        monthly = {
            "2026-08": {"price": 80000, "status": "monthly"},
            "2026-09": {"price": None, "status": "insufficient_data"},
        }
        self.assertEqual(pricing_months_collected(monthly), 1)


class TestCalendarCache(PricingTestBase):
    def test_quote_cache_hit(self):
        url = self.url
        remember_quote(url, date(2026, 8, 1), date(2026, 8, 31), 12345.0)
        hit = load_quote_cache(url, date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual(hit, 12345.0)

    def test_quote_cache_key_identity(self):
        lid = "999"
        key = quote_cache_key(lid, date(2026, 8, 1), date(2026, 8, 31), guests=2, currency="THB")
        self.assertIn("2026-08-01", key)
        self.assertIn("THB", key)

    @mock.patch("pricing_orchestrator.fetch_calendar_days")
    def test_calendar_loaded_once_per_object(self, mock_cal):
        mock_cal.return_value = days_range(date(2026, 8, 1), date(2027, 7, 31))
        parser = MockParser({("2026-08-01", "2026-08-31"): 90000.0})
        collect_primary_month(parser, self.url, {}, {}, today=self.today)
        self.assertEqual(mock_cal.call_count, 1)

    def test_parser_rerun_skips_done(self):
        q = self.queue()
        existing = {"2026-09": {"price": 70000, "status": "monthly"}}
        created = q.enqueue_background_months("A_1", self.url, ["2026-09"], existing=existing)
        self.assertEqual(created, [])


class TestConfig(PricingTestBase):
    def test_months_ahead_twelve(self):
        self.assertEqual(months_ahead(), 12)


if __name__ == "__main__":
    unittest.main()
