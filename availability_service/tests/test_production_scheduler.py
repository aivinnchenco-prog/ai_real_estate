"""Production 48H rolling scheduler tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from availability_service.app.models import (
    AvailabilityObjectState,
    PropertySource,
    RefreshStatus,
    RefreshTier,
    SourceKind,
)
from availability_service.app.production_scheduler import (
    bootstrap_spread_existing,
    ingest_source_catalog,
    run_scheduler_simulation,
    simulate_rolling_window,
)
from availability_service.app.repository import AvailabilityRepository
from availability_service.app.scheduler import AvailabilityScheduler, compute_next_check_at
from availability_service.app.scheduler_policy import (
    PRODUCTION_ROLLING_WINDOW_HOURS,
    compute_retry_next_check_at,
    spread_next_check_schedule,
    tier_after_success,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def test_default_tier_48h_next_check():
    nxt = compute_next_check_at(now=NOW, tier=RefreshTier.H48)
    assert nxt == NOW + timedelta(hours=48)


def test_success_moves_first_refresh_to_48h(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    repo.save_object(
        AvailabilityObjectState(
            object_id="A_NEW",
            refresh_tier=RefreshTier.FIRST_REFRESH,
            next_check_at=NOW,
        ),
        now=NOW,
    )
    repo.enqueue_job("A_NEW", now=NOW)
    scheduler = AvailabilityScheduler(repo)
    nxt = scheduler.schedule_success("A_NEW", now=NOW)
    state = repo.get_object("A_NEW")
    assert state.refresh_tier == RefreshTier.H48
    assert state.refresh_status == RefreshStatus.SUCCESS
    assert nxt == NOW + timedelta(hours=48)
    repo.close()


def test_scheduler_only_due_objects(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    repo.save_object(
        AvailabilityObjectState(object_id="DUE", next_check_at=NOW - timedelta(minutes=1)),
        now=NOW,
    )
    repo.save_object(
        AvailabilityObjectState(
            object_id="LATER",
            next_check_at=NOW + timedelta(hours=1),
        ),
        now=NOW,
    )
    scheduler = AvailabilityScheduler(repo)
    due = scheduler.due_objects(now=NOW)
    assert [o.object_id for o in due] == ["DUE"]
    repo.close()


def test_bootstrap_spreads_1000_objects_across_48h(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    for i in range(1000):
        repo.save_object(
            AvailabilityObjectState(
                object_id=f"OBJ_{i:04d}",
                refresh_tier=RefreshTier.H48,
                next_check_at=NOW,
            ),
            now=NOW,
        )
    count = bootstrap_spread_existing(repo, now=NOW, window_hours=48)
    assert count == 1000
    times = []
    for i in range(1000):
        state = repo.get_object(f"OBJ_{i:04d}")
        assert state is not None
        assert state.next_check_at is not None
        times.append(state.next_check_at)
    assert max(times) <= NOW + timedelta(hours=48) + timedelta(seconds=1)
    assert min(times) >= NOW
    intervals = [(times[i] - times[i - 1]).total_seconds() for i in range(1, len(times))]
    avg_interval = sum(intervals) / len(intervals)
    assert 150 < avg_interval < 200  # ~172.8s for 1000/48h
    repo.close()


def test_bootstrap_not_all_queued_at_once(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    for i in range(10):
        repo.save_object(
            AvailabilityObjectState(object_id=f"O{i}", next_check_at=NOW),
            now=NOW,
        )
    bootstrap_spread_existing(repo, now=NOW, window_hours=48)
    scheduler = AvailabilityScheduler(repo)
    would, _ = scheduler.would_enqueue_due(now=NOW)
    assert would == 1
    repo.close()


def test_new_object_gets_first_refresh(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    prop = PropertySource("A_NEW", "Villa", SourceKind.AIRBNB, "https://airbnb.com/1")
    state = repo.register_new_property(prop, now=NOW)
    assert state.refresh_tier == RefreshTier.FIRST_REFRESH
    assert state.next_check_at == NOW
    repo.close()


def test_new_object_after_success_moves_to_48h(tmp_path):
    assert tier_after_success(RefreshTier.FIRST_REFRESH, SourceKind.AIRBNB) == RefreshTier.H48
    assert tier_after_success(RefreshTier.H48, SourceKind.AIRBNB) == RefreshTier.H48
    assert tier_after_success(RefreshTier.FIRST_REFRESH, SourceKind.FACEBOOK) == RefreshTier.H78


def test_existing_object_no_duplicate(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    prop = PropertySource("A_ONE", "One", SourceKind.AIRBNB, "https://airbnb.com/1")
    repo.register_new_property(prop, now=NOW)
    repo.upsert_property(
        PropertySource("A_ONE", "One Updated", SourceKind.AIRBNB, "https://airbnb.com/2"),
        now=NOW,
    )
    rows = repo._conn.execute(
        "SELECT COUNT(*) AS n FROM availability_objects WHERE object_id='A_ONE'"
    ).fetchone()
    assert int(rows["n"]) == 1
    state = repo.get_object("A_ONE")
    assert state.name == "One Updated"
    assert state.refresh_tier == RefreshTier.FIRST_REFRESH
    repo.close()


def test_error_retry_not_full_48h(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    repo.save_object(
        AvailabilityObjectState(
            object_id="ERR",
            refresh_tier=RefreshTier.H48,
            next_check_at=NOW,
            retry_count=0,
        ),
        now=NOW,
    )
    repo.enqueue_job("ERR", now=NOW)
    scheduler = AvailabilityScheduler(repo)
    nxt, _ = scheduler.schedule_retry("ERR", "timeout", now=NOW)
    assert nxt == NOW + timedelta(minutes=15)
    repo.close()


def test_error_retry_no_tight_loop(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    repo.save_object(
        AvailabilityObjectState(
            object_id="ERR2",
            refresh_tier=RefreshTier.H48,
            next_check_at=NOW,
            retry_count=2,
        ),
        now=NOW,
    )
    repo.enqueue_job("ERR2", now=NOW)
    scheduler = AvailabilityScheduler(repo)
    nxt1, _ = scheduler.schedule_retry("ERR2", "e1", now=NOW)
    state = repo.get_object("ERR2")
    repo.enqueue_job("ERR2", now=NOW)
    nxt2, _ = scheduler.schedule_retry("ERR2", "e2", now=NOW)
    assert nxt2 > nxt1
    assert (nxt2 - NOW) >= timedelta(hours=1)
    repo.close()


def test_ingest_source_new_airbnb_first_refresh(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    props = [
        PropertySource("A_FB", "FB", SourceKind.FACEBOOK, ""),
        PropertySource("A_AB", "Ab", SourceKind.AIRBNB, "https://airbnb.com/1"),
    ]
    result = ingest_source_catalog(None, repo, props, now=NOW)
    assert result.scanned == 2
    assert result.airbnb == 1
    assert result.facebook == 1
    assert sorted(result.new_objects) == ["A_AB", "A_FB"]
    assert sorted(result.notion_create_planned) == ["A_AB", "A_FB"]
    state = repo.get_object("A_AB")
    assert state.refresh_tier == RefreshTier.FIRST_REFRESH
    fb_state = repo.get_object("A_FB")
    assert fb_state.refresh_tier == RefreshTier.FIRST_REFRESH
    repo.close()


def test_ingest_plans_notion_create_for_new_object(tmp_path):
    repo = AvailabilityRepository(tmp_path / "db.sqlite3")
    props = [PropertySource("A_AB2", "Ab", SourceKind.AIRBNB, "https://airbnb.com/1")]
    result = ingest_source_catalog(None, repo, props, now=NOW)
    assert result.new_objects == ["A_AB2"]
    assert result.notion_create_planned == ["A_AB2"]
    assert result.notion_created == []
    repo.close()


def test_simulation_1000_objects():
    sim = run_scheduler_simulation(1000)
    assert sim.object_count == 1000
    assert sim.objects_per_hour_avg == pytest.approx(1000 / 48, rel=0.01)
    assert sim.largest_hourly_bucket <= 25
    assert sim.full_sweep_hours <= PRODUCTION_ROLLING_WINDOW_HOURS
    assert sim.max_simultaneous_workers == 1


def test_spread_schedule_spacing():
    schedule = spread_next_check_schedule(["A", "B", "C"], now=NOW, window_hours=48)
    assert len(schedule) == 3
    assert schedule["A"] == NOW
    delta = (schedule["B"] - schedule["A"]).total_seconds()
    assert 3600 * 48 / 3 == delta


def test_compute_retry_fallback_after_max_short():
    nxt = compute_retry_next_check_at(now=NOW, retry_count=10, fallback_tier=RefreshTier.H48)
    assert nxt == NOW + timedelta(hours=48)
