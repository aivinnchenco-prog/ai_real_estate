from datetime import date, timedelta

import pytest

from availability_service.app.models import PropertySource, SourceKind, calendar_day
from availability_service.app.models import AvailabilityStatus, DailyAvailabilityStatus
from availability_service.app.repository import AvailabilityRepository
from availability_service.app.stay_service import AvailabilityStayService
from availability_service.providers.airbnb import save_provider_calendar


def _days(start: date, count: int, available: bool = True) -> list:
    status = DailyAvailabilityStatus.AVAILABLE if available else DailyAvailabilityStatus.BLOCKED
    return [
        calendar_day(
            start + timedelta(days=i),
            status=status,
            source=SourceKind.AIRBNB,
            source_state="fixture",
        )
        for i in range(count)
    ]


def test_calendar_persistence_after_reopen(tmp_path):
    path = tmp_path / "availability.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(
        PropertySource("A_001", "Test", SourceKind.AIRBNB, "https://airbnb.com/1"),
    )
    start = date(2026, 9, 15)
    repo.upsert_calendar_days("A_001", _days(start, 30))
    repo.close()

    repo2 = AvailabilityRepository(path)
    loaded = repo2.get_calendar_days("A_001", start, date(2026, 10, 15))
    assert len(loaded) == 30
    repo2.close()


def test_duplicate_calendar_upsert(tmp_path):
    path = tmp_path / "availability.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(
        PropertySource("A_001", "Test", SourceKind.AIRBNB, "https://airbnb.com/1"),
    )
    day = calendar_day(date(2026, 9, 15), status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB, source_state="a")
    repo.upsert_calendar_days("A_001", [day])
    repo.upsert_calendar_days(
        "A_001",
        [calendar_day(date(2026, 9, 15), status=DailyAvailabilityStatus.BLOCKED, source=SourceKind.AIRBNB, source_state="b")],
    )
    assert repo.count_calendar_days("A_001") == 1
    rows = repo.get_calendar_days("A_001", date(2026, 9, 15), date(2026, 9, 16))
    assert len(rows) == 1
    assert rows[0].status == DailyAvailabilityStatus.BLOCKED
    assert rows[0].source_state == "b"
    repo.close()


def test_save_provider_calendar_replace(tmp_path):
    path = tmp_path / "availability.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(
        PropertySource("A_002", "Test", SourceKind.AIRBNB, "https://airbnb.com/2"),
    )
    save_provider_calendar(
        repo,
        "A_002",
        _days(date(2026, 9, 1), 5),
        replace=True,
    )
    save_provider_calendar(
        repo,
        "A_002",
        _days(date(2026, 10, 1), 3),
        replace=True,
    )
    assert repo.count_calendar_days("A_002") == 3
    repo.close()


def test_save_provider_calendar_fk_without_parent(tmp_path):
    path = tmp_path / "availability.sqlite3"
    repo = AvailabilityRepository(path)
    with pytest.raises(Exception, match="FOREIGN KEY"):
        save_provider_calendar(
            repo,
            "A_NEW_NO_PARENT",
            _days(date(2026, 9, 1), 3),
            replace=True,
        )
    assert repo.count_calendar_days("A_NEW_NO_PARENT") == 0
    assert repo.get_object("A_NEW_NO_PARENT") is None
    repo.close()


def test_new_object_upsert_before_calendar_fk_pass(tmp_path):
    path = tmp_path / "availability.sqlite3"
    repo = AvailabilityRepository(path)
    object_id = "A_20260719_006"
    prop = PropertySource(object_id, "Villa", SourceKind.AIRBNB, "https://airbnb.com/rooms/1")
    assert repo.get_object(object_id) is None

    repo.upsert_property(prop)
    rows = save_provider_calendar(
        repo,
        object_id,
        _days(date(2026, 9, 1), 5),
        replace=True,
    )
    assert rows == 5
    assert repo.count_calendar_days(object_id) == 5
    assert repo.get_object(object_id) is not None

    repo.upsert_property(prop)
    assert repo.count_calendar_days(object_id) == 5
    repo.close()


def test_duplicate_parent_upsert_idempotent(tmp_path):
    path = tmp_path / "availability.sqlite3"
    repo = AvailabilityRepository(path)
    prop = PropertySource("A_DUP", "One", SourceKind.AIRBNB, "https://airbnb.com/1")
    repo.upsert_property(prop)
    repo.upsert_property(prop)
    repo.upsert_property(PropertySource("A_DUP", "One Updated", SourceKind.AIRBNB, "https://airbnb.com/2"))
    state = repo.get_object("A_DUP")
    assert state is not None
    assert state.name == "One Updated"
    assert state.source_url == "https://airbnb.com/2"
    repo.close()


def test_check_object_stay_via_service(tmp_path):
    path = tmp_path / "availability.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(
        PropertySource("A_003", "Test", SourceKind.AIRBNB, "https://airbnb.com/3"),
    )
    check_in = date(2026, 9, 15)
    check_out = date(2026, 12, 15)
    days = []
    day = check_in
    while day < check_out:
        days.append(
            calendar_day(day, status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB, source_state="fixture")
        )
        day += timedelta(days=1)
    repo.replace_calendar_days("A_003", days)

    service = AvailabilityStayService(repo)
    result = service.check_object_stay("A_003", check_in, 3)
    assert result.status == AvailabilityStatus.AVAILABLE
    assert result.check_out == check_out
    repo.close()
