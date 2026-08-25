from datetime import date, datetime, timezone

from availability_service.app.models import PropertySource, RefreshTier, SourceKind, calendar_day
from availability_service.app.repository import AvailabilityRepository


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def test_sqlite_persistence_after_reopen(tmp_path):
    path = tmp_path / "availability.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(
        PropertySource(
            object_id="A_20260814_001",
            name="Villa Test",
            source=SourceKind.AIRBNB,
            source_url="https://www.airbnb.com/rooms/1",
            notion_page_id="page-1",
        ),
        now=NOW,
        default_tier=RefreshTier.H12,
    )
    repo.set_state("boot", "ok", now=NOW)
    repo.close()

    repo2 = AvailabilityRepository(path)
    state = repo2.get_object("A_20260814_001")
    assert state is not None
    assert state.name == "Villa Test"
    assert state.source == SourceKind.AIRBNB
    assert state.source_url.endswith("/rooms/1")
    assert state.refresh_tier == RefreshTier.FIRST_REFRESH
    assert repo2.get_state("boot") == "ok"
    repo2.close()


def test_remove_object_deletes_related_rows(tmp_path):
    path = tmp_path / "availability.sqlite3"
    repo = AvailabilityRepository(path)
    oid = "A_20260719_007"
    repo.upsert_property(
        PropertySource(
            object_id=oid,
            name="Ghost",
            source=SourceKind.AIRBNB,
            source_url="https://www.airbnb.com/rooms/99",
        ),
        now=NOW,
    )
    from availability_service.app.models import DailyAvailabilityStatus

    repo.upsert_calendar_days(
        oid,
        [calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.AVAILABLE)],
        fetched_at=NOW,
    )
    repo.replace_monthly_rows(oid, [], fetched_at=NOW)
    repo.enqueue_job(oid, now=NOW)
    deleted = repo.remove_object(oid)
    assert deleted["availability_objects"] == 1
    assert deleted["availability_calendar_days"] >= 1
    assert repo.get_object(oid) is None
    repo.close()
