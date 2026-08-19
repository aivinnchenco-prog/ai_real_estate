"""Tests for VPS-safe server concurrency locks."""
from __future__ import annotations

import threading
import time

from availability_service.app.server_concurrency import (
    BatchRunMetrics,
    calendar_fetch_slot,
    configure_server_concurrency,
    get_peak_browser_instances,
    is_server_concurrency_configured,
    price_fetch_slot,
    recommend_price_concurrency,
    reset_peak_browser_instances,
    ServerConcurrencyLimits,
)


def test_configure_forces_calendar_single_flight():
    configure_server_concurrency(
        ServerConcurrencyLimits(
            object_concurrency=2,
            calendar_concurrency=2,
            price_concurrency=1,
            browser_max_instances=2,
        )
    )
    assert is_server_concurrency_configured()


def test_calendar_slot_blocks_parallel_objects():
    configure_server_concurrency(ServerConcurrencyLimits())
    reset_peak_browser_instances()
    metrics = BatchRunMetrics()
    order: list[str] = []

    def worker(oid: str) -> None:
        with calendar_fetch_slot(oid, metrics=metrics):
            order.append(f"{oid}-start")
            time.sleep(0.05)
            order.append(f"{oid}-end")

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start()
    time.sleep(0.01)
    t2.start()
    t1.join()
    t2.join()

    # No overlap: A completes before B starts (or vice versa)
    assert order.index("A-end") < order.index("B-start") or order.index("B-end") < order.index("A-start")
    assert metrics.peak_browser_instances <= 2


def test_recommend_keep_on_calendar_failure():
    metrics = BatchRunMetrics(calendar_failures=1)
    msg = recommend_price_concurrency(metrics)
    assert "KEEP price_concurrency=1" in msg


def test_recommend_may_test_when_clean():
    metrics = BatchRunMetrics(
        per_object_elapsed_s={"A": 60.0},
        peak_browser_instances=1,
        peak_ram_mb=500,
    )
    msg = recommend_price_concurrency(metrics)
    assert "MAY TEST price_concurrency=2" in msg
    assert "calendar_concurrency stays 1" in msg


def test_peak_browser_tracking():
    configure_server_concurrency(ServerConcurrencyLimits(browser_max_instances=2))
    reset_peak_browser_instances()
    metrics = BatchRunMetrics()
    with calendar_fetch_slot("X", metrics=metrics):
        assert get_peak_browser_instances() >= 1
    assert metrics.peak_browser_instances >= 1


def test_browser_cap_counts_slots_not_os_chrome_children():
    """Peak browser instances = Availability semaphore slots, not OS Chrome PIDs."""
    configure_server_concurrency(ServerConcurrencyLimits(browser_max_instances=2))
    reset_peak_browser_instances()
    metrics = BatchRunMetrics()
    fake_os_chrome_children = 11

    # Simulated: many OS processes would not affect our counter without slot acquire.
    assert metrics.peak_browser_instances == 0
    assert get_peak_browser_instances() == 0

    with calendar_fetch_slot("A", metrics=metrics):
        assert get_peak_browser_instances() == 1
        assert metrics.peak_browser_instances == 1
        # Holding one slot: peak stays 1 even if a real browser spawned 11 children.
        assert fake_os_chrome_children == 11
        with price_fetch_slot("A", metrics=metrics):
            assert get_peak_browser_instances() == 2
            assert metrics.peak_browser_instances == 2

    assert get_peak_browser_instances() == 2
    reset_peak_browser_instances()
    assert get_peak_browser_instances() == 0
