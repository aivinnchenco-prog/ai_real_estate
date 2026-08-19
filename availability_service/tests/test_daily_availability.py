"""Tests for daily availability DB, date ranges, freshness, summaries, internal APIs."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from availability_service.app.date_ranges import build_date_ranges
from availability_service.app.models import (
    AvailabilityFreshness,
    AvailabilityStatus,
    DailyAvailabilityStatus,
    PropertySource,
    SourceKind,
    calendar_day,
)
from availability_service.app.repository import AvailabilityRepository
from availability_service.app.stay_matcher import check_stay_availability
from availability_service.app.stay_service import AvailabilityStayService
from availability_service.app.stay_summary import format_stay_availability_summary


def _sep_blocked_example() -> dict[date, DailyAvailabilityStatus]:
    """Sep 15–Dec 14 with blocked 17–18 Sep and 4–7 Oct."""
    cal: dict[date, DailyAvailabilityStatus] = {}
    start = date(2026, 9, 15)
    end = date(2026, 12, 15)
    day = start
    while day < end:
        cal[day] = DailyAvailabilityStatus.AVAILABLE
        day += timedelta(days=1)
    for blocked in (date(2026, 9, 17), date(2026, 9, 18)):
        cal[blocked] = DailyAvailabilityStatus.BLOCKED
    for blocked in (
        date(2026, 10, 4),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    ):
        cal[blocked] = DailyAvailabilityStatus.BLOCKED
    return cal


def test_stay_available_days_only():
    check_in = date(2026, 9, 15)
    cal: dict[date, DailyAvailabilityStatus] = {}
    end = date(2026, 12, 15)
    day = check_in
    while day < end:
        cal[day] = DailyAvailabilityStatus.AVAILABLE
        day += timedelta(days=1)
    result = check_stay_availability("A_001", check_in, 3, cal, freshness=AvailabilityFreshness.FRESH)
    assert result.status == AvailabilityStatus.AVAILABLE


def test_one_blocked_date_unavailable():
    cal = _sep_blocked_example()
    result = check_stay_availability("A_001", date(2026, 9, 15), 3, cal)
    assert result.status == AvailabilityStatus.UNAVAILABLE
    assert date(2026, 9, 17) in result.blocked_dates


def test_consecutive_blocked_single_range():
    cal = _sep_blocked_example()
    result = check_stay_availability("A_001", date(2026, 9, 15), 3, cal)
    sep_range = next(r for r in result.blocked_ranges if r["from"].startswith("2026-09"))
    assert sep_range == {"from": "2026-09-17", "to": "2026-09-18"}


def test_two_blocked_periods_two_ranges():
    cal = _sep_blocked_example()
    result = check_stay_availability("A_001", date(2026, 9, 15), 3, cal)
    assert len(result.blocked_ranges) == 2


def test_missing_dates_unknown():
    check_in = date(2026, 9, 15)
    cal = {check_in: DailyAvailabilityStatus.AVAILABLE}
    result = check_stay_availability("A_001", check_in, 1, cal)
    assert result.status == AvailabilityStatus.UNKNOWN
    assert result.unknown_dates


def test_blocked_plus_missing_unavailable():
    check_in = date(2026, 9, 15)
    cal = {
        check_in: DailyAvailabilityStatus.AVAILABLE,
        date(2026, 9, 20): DailyAvailabilityStatus.BLOCKED,
    }
    result = check_stay_availability("A_001", check_in, 1, cal)
    assert result.status == AvailabilityStatus.UNAVAILABLE


def test_freshness_fresh(tmp_path):
    path = tmp_path / "db.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(PropertySource("A_001", "T", SourceKind.AIRBNB, ""))
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    repo.upsert_calendar_days(
        "A_001",
        [calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB)],
        fetched_at=now,
        last_successful_refresh=now,
    )
    assert repo.get_object_calendar_freshness("A_001", now=now) == AvailabilityFreshness.FRESH
    repo.close()


def test_freshness_stale(tmp_path):
    path = tmp_path / "db.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(PropertySource("A_001", "T", SourceKind.AIRBNB, ""))
    old = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    repo.upsert_calendar_days(
        "A_001",
        [calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB)],
        fetched_at=old,
        last_successful_refresh=old,
    )
    assert repo.get_object_calendar_freshness("A_001", now=now) == AvailabilityFreshness.STALE
    repo.close()


def test_freshness_missing(tmp_path):
    path = tmp_path / "db.sqlite3"
    repo = AvailabilityRepository(path)
    assert repo.get_object_calendar_freshness("NONE") == AvailabilityFreshness.MISSING
    repo.close()


def test_migration_available_one_to_available(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = __import__("sqlite3").connect(path)
    conn.execute(
        """
        CREATE TABLE availability_objects (
            object_id TEXT PRIMARY KEY, name TEXT, source TEXT, source_url TEXT,
            notion_page_id TEXT, refresh_tier TEXT, last_checked_at TEXT,
            next_check_at TEXT, refresh_status TEXT, source_status TEXT,
            last_error TEXT, retry_count INTEGER, created_at TEXT, updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE availability_calendar_days (
            object_id TEXT, date TEXT, available INTEGER, source_state TEXT,
            fetched_at TEXT, PRIMARY KEY (object_id, date)
        )
        """
    )
    conn.execute(
        "INSERT INTO availability_objects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A_001", "T", "AIRBNB", "", "", "12H", None, None, "IDLE", "UNKNOWN", "", 0, "t", "t"),
    )
    conn.execute(
        "INSERT INTO availability_calendar_days VALUES (?,?,?,?,?)",
        ("A_001", "2026-09-01", 1, "legacy", "2026-08-14T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    repo = AvailabilityRepository(path)
    rows = repo.get_calendar_days("A_001", date(2026, 9, 1), date(2026, 9, 2))
    assert rows[0].status == DailyAvailabilityStatus.AVAILABLE
    repo.close()


def test_migration_available_zero_to_blocked(tmp_path):
    path = tmp_path / "legacy0.sqlite3"
    conn = __import__("sqlite3").connect(path)
    conn.executescript(
        """
        CREATE TABLE availability_objects (
            object_id TEXT PRIMARY KEY, name TEXT, source TEXT, source_url TEXT,
            notion_page_id TEXT, refresh_tier TEXT, last_checked_at TEXT,
            next_check_at TEXT, refresh_status TEXT, source_status TEXT,
            last_error TEXT, retry_count INTEGER, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE availability_calendar_days (
            object_id TEXT, date TEXT, available INTEGER, source_state TEXT,
            fetched_at TEXT, PRIMARY KEY (object_id, date)
        );
        """
    )
    conn.execute(
        "INSERT INTO availability_objects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A_001", "T", "AIRBNB", "", "", "12H", None, None, "IDLE", "UNKNOWN", "", 0, "t", "t"),
    )
    conn.execute(
        "INSERT INTO availability_calendar_days VALUES (?,?,?,?,?)",
        ("A_001", "2026-09-02", 0, "legacy", "2026-08-14T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    repo = AvailabilityRepository(path)
    rows = repo.get_calendar_days("A_001", date(2026, 9, 2), date(2026, 9, 3))
    assert rows[0].status == DailyAvailabilityStatus.BLOCKED
    repo.close()


def test_existing_calendar_survives_migration(tmp_path):
    path = tmp_path / "survive.sqlite3"
    conn = __import__("sqlite3").connect(path)
    conn.executescript(
        """
        CREATE TABLE availability_objects (
            object_id TEXT PRIMARY KEY, name TEXT, source TEXT, source_url TEXT,
            notion_page_id TEXT, refresh_tier TEXT, last_checked_at TEXT,
            next_check_at TEXT, refresh_status TEXT, source_status TEXT,
            last_error TEXT, retry_count INTEGER, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE availability_calendar_days (
            object_id TEXT, date TEXT, available INTEGER, source_state TEXT,
            fetched_at TEXT, PRIMARY KEY (object_id, date)
        );
        """
    )
    conn.execute(
        "INSERT INTO availability_objects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A_001", "T", "AIRBNB", "", "", "12H", None, None, "IDLE", "UNKNOWN", "", 0, "t", "t"),
    )
    for i in range(5):
        d = date(2026, 9, 1) + timedelta(days=i)
        conn.execute(
            "INSERT INTO availability_calendar_days VALUES (?,?,?,?,?)",
            ("A_001", d.isoformat(), 1, "legacy", "2026-08-14T00:00:00+00:00"),
        )
    conn.commit()
    conn.close()

    repo = AvailabilityRepository(path)
    assert repo.count_calendar_days("A_001") == 5
    assert repo._integrity_check() == "ok"
    repo.close()


def test_duplicate_object_date_impossible(tmp_path):
    path = tmp_path / "dup.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(PropertySource("A_001", "T", SourceKind.AIRBNB, ""))
    day = calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB)
    repo.upsert_calendar_days("A_001", [day])
    repo.upsert_calendar_days(
        "A_001",
        [calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.BLOCKED, source=SourceKind.AIRBNB)],
    )
    assert repo.count_calendar_days("A_001") == 1
    rows = repo.get_calendar_days("A_001", date(2026, 9, 1), date(2026, 9, 2))
    assert rows[0].status == DailyAvailabilityStatus.BLOCKED
    repo.close()


def test_check_out_exclusive():
    cal = _sep_blocked_example()
    result = check_stay_availability("A_001", date(2026, 9, 15), 3, cal)
    assert result.checked_days[-1] == date(2026, 12, 14)
    assert date(2026, 12, 15) not in result.checked_days


def test_ru_summary_available():
    check_in = date(2026, 9, 15)
    cal: dict[date, DailyAvailabilityStatus] = {}
    end = date(2026, 12, 15)
    day = check_in
    while day < end:
        cal[day] = DailyAvailabilityStatus.AVAILABLE
        day += timedelta(days=1)
    result = check_stay_availability("A_001", check_in, 3, cal)
    text = format_stay_availability_summary(result, "ru")
    assert "свободен" in text.lower() or "Свободен" in text


def test_ru_summary_unavailable_ranges():
    cal = _sep_blocked_example()
    result = check_stay_availability("A_001", date(2026, 9, 15), 3, cal)
    text = format_stay_availability_summary(result, "ru")
    assert "17" in text and "сентября" in text
    assert "4" in text and "октября" in text


def test_ru_summary_unknown():
    check_in = date(2026, 9, 15)
    end = date(2026, 11, 16)
    cal: dict[date, DailyAvailabilityStatus] = {}
    day = check_in
    while day < date(2026, 10, 31):
        cal[day] = DailyAvailabilityStatus.AVAILABLE
        day += timedelta(days=1)
    result = check_stay_availability("A_001", check_in, 3, cal)
    text = format_stay_availability_summary(result, "ru")
    assert "нет актуальных данных" in text


def test_en_summary():
    cal = _sep_blocked_example()
    result = check_stay_availability("A_001", date(2026, 9, 15), 3, cal)
    text = format_stay_availability_summary(result, "en")
    assert "blocked dates" in text.lower()


def test_agent_internal_payload(tmp_path):
    path = tmp_path / "agent.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(PropertySource("A_001", "T", SourceKind.AIRBNB, ""))
    cal = _sep_blocked_example()
    days = [
        calendar_day(d, status=s, source=SourceKind.AIRBNB)
        for d, s in cal.items()
    ]
    repo.replace_calendar_days("A_001", days, fetched_at=datetime.now(timezone.utc))
    service = AvailabilityStayService(repo)
    payload = service.get_stay_availability_for_agent("A_001", date(2026, 9, 15), 3, locale="ru")
    assert payload["status"] == "UNAVAILABLE"
    assert payload["freshness"] in ("FRESH", "STALE")
    assert payload["blocked_ranges"] == [
        {"from": "2026-09-17", "to": "2026-09-18"},
        {"from": "2026-10-04", "to": "2026-10-07"},
    ]
    assert "summary" in payload
    assert payload["summary"]
    repo.close()


def test_website_calendar_internal(tmp_path):
    path = tmp_path / "web.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(PropertySource("A_001", "T", SourceKind.AIRBNB, ""))
    repo.upsert_calendar_days(
        "A_001",
        [
            calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB),
            calendar_day(date(2026, 9, 2), status=DailyAvailabilityStatus.BLOCKED, source=SourceKind.AIRBNB),
        ],
    )
    service = AvailabilityStayService(repo)
    view = service.get_calendar_view("A_001", date(2026, 9, 1), date(2026, 9, 3))
    assert view["object_id"] == "A_001"
    assert len(view["days"]) == 2
    assert view["days"][0]["status"] == "AVAILABLE"
    assert view["days"][1]["status"] == "BLOCKED"
    repo.close()


def test_build_date_ranges_single_day():
    ranges = build_date_ranges([date(2026, 9, 17)])
    assert ranges == [{"from": "2026-09-17", "to": "2026-09-17"}]


def test_build_date_ranges_multi():
    dates = [
        date(2026, 9, 17),
        date(2026, 9, 18),
        date(2026, 10, 4),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    ]
    ranges = build_date_ranges(dates)
    assert ranges == [
        {"from": "2026-09-17", "to": "2026-09-18"},
        {"from": "2026-10-04", "to": "2026-10-07"},
    ]


def test_replace_calendar_range_other_object_preserved(tmp_path):
    path = tmp_path / "range.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(PropertySource("A_001", "T", SourceKind.AIRBNB, ""))
    repo.upsert_property(PropertySource("A_002", "T", SourceKind.AIRBNB, ""))
    repo.upsert_calendar_days(
        "A_001",
        [calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB)],
    )
    repo.upsert_calendar_days(
        "A_002",
        [calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.BLOCKED, source=SourceKind.AIRBNB)],
    )
    repo.replace_calendar_range(
        "A_001",
        date(2026, 9, 1),
        date(2026, 9, 2),
        [calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.BLOCKED, source=SourceKind.AIRBNB)],
    )
    assert repo.count_calendar_days("A_001") == 1
    assert repo.count_calendar_days("A_002") == 1
    repo.close()


def test_freshness_fallback_without_objects_row(tmp_path):
    path = tmp_path / "fallback.sqlite3"
    repo = AvailabilityRepository(path)
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    repo.upsert_property(PropertySource("A_001", "T", SourceKind.AIRBNB, ""))
    repo.upsert_calendar_days(
        "A_001",
        [calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB)],
        fetched_at=now,
        last_successful_refresh=now,
    )
    repo._conn.execute("PRAGMA foreign_keys=OFF")
    repo._conn.execute("DELETE FROM availability_objects WHERE object_id = ?", ("A_001",))
    repo._conn.commit()
    repo._conn.execute("PRAGMA foreign_keys=ON")
    freshness = repo.get_object_calendar_freshness("A_001", now=now)
    assert freshness == AvailabilityFreshness.FRESH
    repo.validate_freshness_invariant("A_001")
    repo.close()


def test_freshness_stale_when_no_usable_timestamp(tmp_path):
    path = tmp_path / "nostamp.sqlite3"
    repo = AvailabilityRepository(path)
    repo.upsert_property(PropertySource("A_001", "T", SourceKind.AIRBNB, ""))
    repo.upsert_calendar_days(
        "A_001",
        [calendar_day(date(2026, 9, 1), status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB)],
    )
    repo._conn.execute(
        "UPDATE availability_calendar_days SET last_successful_refresh=NULL, fetched_at='' WHERE object_id=?",
        ("A_001",),
    )
    repo._conn.execute(
        "UPDATE availability_objects SET last_calendar_refresh_at=NULL WHERE object_id=?",
        ("A_001",),
    )
    repo._conn.commit()
    assert repo.get_object_calendar_freshness("A_001") == AvailabilityFreshness.STALE
    repo.validate_freshness_invariant("A_001")
    repo.close()


def test_unknown_day_status_freshness_not_missing(tmp_path):
    path = tmp_path / "unkday.sqlite3"
    repo = AvailabilityRepository(path)
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    repo.upsert_property(PropertySource("A_001", "T", SourceKind.AIRBNB, ""))
    repo.upsert_calendar_days(
        "A_001",
        [
            calendar_day(
                date(2026, 9, 1),
                status=DailyAvailabilityStatus.UNKNOWN,
                source=SourceKind.AIRBNB,
            ),
        ],
        fetched_at=now,
        last_successful_refresh=now,
    )
    freshness = repo.get_object_calendar_freshness("A_001", now=now)
    assert freshness == AvailabilityFreshness.FRESH
    repo.validate_freshness_invariant("A_001")
    repo.close()


def _legacy_calendar_db(path, object_id: str, object_source: str, available: int = 1):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE availability_objects (
            object_id TEXT PRIMARY KEY, name TEXT, source TEXT, source_url TEXT,
            notion_page_id TEXT, refresh_tier TEXT, last_checked_at TEXT,
            next_check_at TEXT, refresh_status TEXT, source_status TEXT,
            last_error TEXT, retry_count INTEGER, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE availability_calendar_days (
            object_id TEXT, date TEXT, available INTEGER, source_state TEXT,
            fetched_at TEXT, PRIMARY KEY (object_id, date)
        );
        """
    )
    conn.execute(
        "INSERT INTO availability_objects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            object_id,
            "T",
            object_source,
            "",
            "",
            "12H",
            None,
            None,
            "IDLE",
            "UNKNOWN",
            "",
            0,
            "t",
            "t",
        ),
    )
    conn.execute(
        "INSERT INTO availability_calendar_days VALUES (?,?,?,?,?)",
        (object_id, "2026-09-01", available, "legacy", "2026-08-14T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()


def test_migration_legacy_airbnb_object_source(tmp_path):
    path = tmp_path / "mig_airbnb.sqlite3"
    _legacy_calendar_db(path, "A_20260810_003", "AIRBNB")
    repo = AvailabilityRepository(path)
    rows = repo.get_calendar_days("A_20260810_003", date(2026, 9, 1), date(2026, 9, 2))
    assert rows[0].source == SourceKind.AIRBNB
    assert rows[0].status == DailyAvailabilityStatus.AVAILABLE
    repo.close()


def test_migration_legacy_unknown_object_source(tmp_path):
    path = tmp_path / "mig_unknown.sqlite3"
    _legacy_calendar_db(path, "OBJ_UNKNOWN", "UNKNOWN")
    repo = AvailabilityRepository(path)
    rows = repo.get_calendar_days("OBJ_UNKNOWN", date(2026, 9, 1), date(2026, 9, 2))
    assert rows[0].source == SourceKind.UNKNOWN
    repo.close()


def test_migration_no_blanket_airbnb_for_facebook(tmp_path):
    path = tmp_path / "mig_fb.sqlite3"
    _legacy_calendar_db(path, "F_20260811_001", "FACEBOOK")
    repo = AvailabilityRepository(path)
    rows = repo.get_calendar_days("F_20260811_001", date(2026, 9, 1), date(2026, 9, 2))
    assert rows[0].source == SourceKind.FACEBOOK
    repo.close()
