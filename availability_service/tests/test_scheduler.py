from datetime import datetime, timedelta, timezone

from availability_service.app.models import AvailabilityObjectState, RefreshTier, SourceKind
from availability_service.app.repository import AvailabilityRepository
from availability_service.app.scheduler import AvailabilityScheduler, compute_next_check_at
from availability_service.app.scheduler_policy import (
    AIRBNB_ERROR_RETRY_FALLBACK_HOURS,
    RETRY_DELAY_MINUTES,
    airbnb_check_interval_hours,
    compute_retry_next_check_at,
)


NOW = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)


def _seed(repo: AvailabilityRepository, object_id: str, tier: RefreshTier) -> None:
    repo.save_object(
        AvailabilityObjectState(
            object_id=object_id,
            refresh_tier=tier,
            next_check_at=NOW,
        ),
        now=NOW,
    )


def test_1h_next_check_at():
    nxt = compute_next_check_at(now=NOW, tier=RefreshTier.H1)
    assert nxt == datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc)


def test_12h_next_check_at():
    nxt = compute_next_check_at(now=NOW, tier=RefreshTier.H12)
    assert nxt == datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)


def test_airbnb_success_next_check_96h_default():
    nxt = compute_next_check_at(now=NOW, tier=RefreshTier.H48)
    assert nxt == NOW + timedelta(hours=airbnb_check_interval_hours())
    assert airbnb_check_interval_hours() == 96


def test_airbnb_success_next_check_env_override(monkeypatch):
    monkeypatch.setenv("AIRBNB_CHECK_INTERVAL_HOURS", "120")
    nxt = compute_next_check_at(now=NOW, tier=RefreshTier.H48)
    assert nxt == NOW + timedelta(hours=120)


def test_24h_next_check_at():
    nxt = compute_next_check_at(now=NOW, tier=RefreshTier.H24)
    assert nxt == datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)


def test_duplicate_refresh_job_protection(tmp_path):
    repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
    _seed(repo, "A_20260814_001", RefreshTier.H12)
    scheduler = AvailabilityScheduler(repo)
    first, created_first = repo.enqueue_job("A_20260814_001", now=NOW)
    second, created_second = repo.enqueue_job("A_20260814_001", now=NOW)
    assert created_first is True
    assert created_second is False
    assert first.job_id == second.job_id
    enqueued = scheduler.enqueue_due(now=NOW)
    assert enqueued[0][1] is False
    repo.close()


def test_would_enqueue_preview_without_creating_jobs(tmp_path):
    repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
    _seed(repo, "A_20260814_003", RefreshTier.H12)
    _seed(repo, "F_20260814_004", RefreshTier.H12)
    scheduler = AvailabilityScheduler(repo)
    jobs_before = repo.count_active_jobs()
    would, ids = scheduler.would_enqueue_due(now=NOW, limit=2)
    jobs_after = repo.count_active_jobs()
    assert would == 2
    assert len(ids) == 2
    assert jobs_after == jobs_before == 0


def test_retry_state(tmp_path):
    repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
    _seed(repo, "F_20260814_002", RefreshTier.H12)
    repo.enqueue_job("F_20260814_002", now=NOW)
    scheduler = AvailabilityScheduler(repo)
    nxt, retry_count = scheduler.schedule_retry("F_20260814_002", "timeout", now=NOW)
    state = repo.get_object("F_20260814_002")
    assert retry_count == 1
    assert state.retry_count == 1
    assert state.refresh_status.value == "ERROR"
    assert state.last_error == "timeout"
    assert state.next_check_at == nxt
    nxt2, retry_count2 = scheduler.schedule_retry("F_20260814_002", "timeout-2", now=NOW)
    assert retry_count2 == 2
    assert nxt2 > nxt
    repo.close()


def test_airbnb_retry_uses_short_delays_not_success_interval(tmp_path, monkeypatch):
    monkeypatch.delenv("AIRBNB_CHECK_INTERVAL_HOURS", raising=False)
    repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
    _seed(repo, "A_20260814_010", RefreshTier.H48)
    repo.enqueue_job("A_20260814_010", now=NOW)
    scheduler = AvailabilityScheduler(repo)
    nxt, _ = scheduler.schedule_retry("A_20260814_010", "parse", now=NOW)
    assert nxt == NOW + timedelta(minutes=RETRY_DELAY_MINUTES[0])
    assert nxt < NOW + timedelta(hours=airbnb_check_interval_hours())
    repo.close()


def test_airbnb_exhausted_retry_fallback_48h_not_96h(tmp_path):
    nxt = compute_retry_next_check_at(now=NOW, retry_count=99, fallback_tier=RefreshTier.H48)
    assert nxt == NOW + timedelta(hours=AIRBNB_ERROR_RETRY_FALLBACK_HOURS)
    assert AIRBNB_ERROR_RETRY_FALLBACK_HOURS == 48
