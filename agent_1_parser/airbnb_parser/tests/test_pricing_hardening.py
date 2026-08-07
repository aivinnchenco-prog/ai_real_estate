"""Staging hardening tests: global gate, startup, calendar refresh, status semantics."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from monthly_pricing import month_keys_ahead
from pricing_calendar import (
    month_has_calendar_data,
    needs_calendar_refresh,
)
from pricing_gate import get_max_concurrent, reset_instrumentation, run_pricing
from pricing_queue import PricingQueue
from pricing_state import compute_pricing_status, serialize_calendar
from pricing_worker import (
    _WORKER_THREAD,
    bootstrap_background_pricing,
    process_one_job,
    stop_background_worker,
)


class HardeningBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.queue_path = Path(self._tmpdir.name) / "pricing_queue.json"
        self._old = {
            "PRICE_QUEUE_PATH": config.PRICE_QUEUE_PATH,
            "PRICE_COLLECT_ENABLED": config.PRICE_COLLECT_ENABLED,
            "PRICE_NOTION_SYNC_ENABLED": config.PRICE_NOTION_SYNC_ENABLED,
            "PRICE_MONTHS_AHEAD": config.PRICE_MONTHS_AHEAD,
        }
        config.PRICE_QUEUE_PATH = str(self.queue_path)
        config.PRICE_COLLECT_ENABLED = True
        config.PRICE_NOTION_SYNC_ENABLED = False
        config.PRICE_MONTHS_AHEAD = 12
        reset_instrumentation()
        stop_background_worker()

    def tearDown(self):
        stop_background_worker()
        for k, v in self._old.items():
            setattr(config, k, v)
        self._tmpdir.cleanup()

    def queue(self) -> PricingQueue:
        return PricingQueue(self.queue_path)


class MockParser:
    def __init__(self, prices=None, blocked=False):
        self._prices = prices or {}
        self._blocked = blocked

    def _fetch_price_for_period_once(self, url, check_in, check_out):
        if self._blocked:
            return None, True
        return self._prices.get((str(check_in), str(check_out))), False


class TestGlobalConcurrency(HardeningBase):
    def test_five_objects_max_concurrent_one(self):
        reset_instrumentation()
        start = threading.Barrier(5)

        def one_object(_i: int) -> None:
            start.wait()

            def work() -> int:
                time.sleep(0.04)
                return 1

            run_pricing(work, primary=True)

        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(one_object, range(5)))

        self.assertEqual(get_max_concurrent(), 1)

    def test_primary_priority_over_background(self):
        reset_instrumentation()
        order: list[str] = []
        lock = threading.Lock()
        bg_started = threading.Event()
        bg_release = threading.Event()

        def background() -> None:
            def work() -> None:
                with lock:
                    order.append("bg_start")
                bg_started.set()
                bg_release.wait()
                with lock:
                    order.append("bg_end")

            run_pricing(work, primary=False)

        def primary() -> None:
            bg_started.wait()

            def work() -> None:
                with lock:
                    order.append("primary")

            run_pricing(work, primary=True)

        t_bg = threading.Thread(target=background)
        t_bg.start()
        time.sleep(0.02)
        t_pri = threading.Thread(target=primary)
        t_pri.start()
        time.sleep(0.02)
        bg_release.set()
        t_pri.join(timeout=5)
        t_bg.join(timeout=5)

        self.assertEqual(order[:2], ["bg_start", "bg_end"])
        self.assertEqual(order[2], "primary")


class TestStartup(HardeningBase):
    def test_bootstrap_starts_worker_on_pending_queue(self):
        q = self.queue()
        q.enqueue_background_months("A_1", "https://www.airbnb.com/rooms/1", ["2026-09"])
        q.save()
        import pricing_worker as pw

        with mock.patch.object(pw, "process_one_job", return_value=True):
            self.assertTrue(bootstrap_background_pricing())
            self.assertIsNotNone(pw._WORKER_THREAD)
            self.assertTrue(pw._WORKER_THREAD.is_alive())
        stop_background_worker()
        time.sleep(0.05)


class TestCalendarRefresh(HardeningBase):
    def setUp(self):
        super().setUp()
        self.today = date(2026, 7, 16)

    def test_month_present_no_refresh(self):
        avail = {date(2026, 9, d): True for d in range(1, 31)}
        obj = {
            "calendar": serialize_calendar(avail),
            "calendar_saved_at": time.time(),
        }
        need, reason = needs_calendar_refresh(obj, 2026, 9)
        self.assertFalse(need)
        self.assertEqual(reason, "")

    def test_month_absent_needs_refresh(self):
        avail = {date(2026, 8, d): True for d in range(1, 32)}
        obj = {"calendar": serialize_calendar(avail), "calendar_saved_at": time.time()}
        need, reason = needs_calendar_refresh(obj, 2026, 9)
        self.assertTrue(need)
        self.assertEqual(reason, "month_missing")

    def test_stale_snapshot_needs_refresh(self):
        avail = {date(2026, 9, d): True for d in range(1, 31)}
        obj = {
            "calendar": serialize_calendar(avail),
            "calendar_saved_at": time.time() - 90000,
        }
        need, reason = needs_calendar_refresh(obj, 2026, 9)
        self.assertTrue(need)
        self.assertEqual(reason, "stale")

    @mock.patch("pricing_worker.refresh_calendar_snapshot")
    def test_absent_month_triggers_refresh_not_insufficient(self, mock_refresh):
        mock_refresh.return_value = {
            date(2026, 9, d): True for d in range(1, 31)
        }
        q = self.queue()
        q.upsert_object(
            "A_1",
            listing_url="https://www.airbnb.com/rooms/1",
            calendar=serialize_calendar(
                {date(2026, 8, d): True for d in range(1, 32)}
            ),
            calendar_saved_at=time.time(),
        )
        q.enqueue_background_months("A_1", "https://www.airbnb.com/rooms/1", ["2026-09"])
        q.save()
        job = q.pick_next_job()
        parser = MockParser({("2026-09-01", "2026-09-30"): 60000.0})
        process_one_job(job, parser=parser, queue=q)
        q.load()
        self.assertEqual(mock_refresh.call_count, 1)
        obj = q.get_object("A_1")
        self.assertTrue(entry_has_price(obj["monthly_prices"].get("2026-09")))
        self.assertEqual(q.jobs_for_object("A_1")[0]["status"], "done")

    @mock.patch("pricing_worker.refresh_calendar_snapshot")
    def test_fresh_snapshot_skips_extra_refresh(self, mock_refresh):
        avail = {date(2026, 9, d): True for d in range(1, 31)}
        q = self.queue()
        q.upsert_object(
            "A_1",
            listing_url="https://www.airbnb.com/rooms/1",
            calendar=serialize_calendar(avail),
            calendar_saved_at=time.time(),
        )
        q.enqueue_background_months("A_1", "https://www.airbnb.com/rooms/1", ["2026-09"])
        job = q.pick_next_job()
        parser = MockParser({("2026-09-01", "2026-09-30"): 60000.0})
        process_one_job(job, parser=parser, queue=q)
        mock_refresh.assert_not_called()


def entry_has_price(entry):
    from monthly_pricing import entry_has_price as _e

    return _e(entry)


class TestCompletionSemantics(HardeningBase):
    def setUp(self):
        super().setUp()
        self.today = date(2026, 7, 16)
        self.keys = month_keys_ahead(self.today, 12)

    def test_ten_done_two_insufficient_is_partial(self):
        monthly = {}
        for k in self.keys[:10]:
            monthly[k] = {"price": 50000, "status": "monthly"}
        for k in self.keys[10:]:
            monthly[k] = {"price": None, "status": "insufficient_data"}
        st = compute_pricing_status(
            monthly,
            months_target=12,
            has_active_jobs=False,
            expected_keys=self.keys,
        )
        self.assertEqual(st, "partial")

    def test_twelve_done_is_complete(self):
        monthly = {k: {"price": 50000, "status": "monthly"} for k in self.keys}
        st = compute_pricing_status(
            monthly,
            months_target=12,
            has_active_jobs=False,
            expected_keys=self.keys,
        )
        self.assertEqual(st, "complete")

    def test_active_jobs_is_collecting(self):
        monthly = {self.keys[0]: {"price": 50000, "status": "monthly"}}
        st = compute_pricing_status(
            monthly,
            months_target=12,
            has_active_jobs=True,
            expected_keys=self.keys,
        )
        self.assertEqual(st, "collecting")


class TestRestartResume(HardeningBase):
    def test_restart_preserves_pending(self):
        q = self.queue()
        q.enqueue_background_months("A_1", "https://www.airbnb.com/rooms/1", ["2026-09", "2026-10"])
        q.save()
        q2 = PricingQueue(self.queue_path)
        self.assertEqual(len(q2._eligible_jobs()), 2)


if __name__ == "__main__":
    unittest.main()
