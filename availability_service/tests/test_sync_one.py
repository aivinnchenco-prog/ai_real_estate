"""Tests for sync-one safety gates and Notion writer narrow path."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from availability_service.app.config import AvailabilityConfig
from availability_service.app.models import (
    CalendarDay,
    PropertySource,
    RefreshStatus,
    RefreshTier,
    SourceKind,
    SourceStatus,
    TargetFieldMapping,
    WindowStartMode,
    build_month_window,
    calendar_day,
    get_effective_window_start,
    DailyAvailabilityStatus,
)
from availability_service.app.notion_reader import FieldMatch
from availability_service.app.notion_writer import NotionWriter, SyncOneWriteRefused
from availability_service.app.repository import AvailabilityRepository
from availability_service.app.sync_one import run_sync_one, SYNC_ONE_ALLOWED_OBJECT_IDS


def _config(tmp_path: Path) -> AvailabilityConfig:
    return AvailabilityConfig(
        enabled=False,
        dry_run=True,
        notion_api_key="ntn_test",
        source_database_id="e817ce50-e788-4992-8b86-c9c9fc1fbcf7",
        target_database_id="3bc2c251-5061-80c5-a13e-d394ec1a4eb1",
        target_data_source_id="",
        runtime_dir=tmp_path,
        sqlite_path=tmp_path / "availability.sqlite3",
        default_refresh_tier=RefreshTier.H12,
        max_concurrency=2,
        dry_run_batch_size=25,
        object_concurrency=1,
        calendar_concurrency=1,
        price_concurrency=1,
        browser_max_instances=2,
        batch_safe_max_objects=5,
        window_start_mode=WindowStartMode.NEXT_MONTH,
        airbnb_enabled=False,
    )


def _target_mapping() -> TargetFieldMapping:
    mapped = FieldMatch("x", "Object ID", "title", "mapped", ("Object ID",))
    return TargetFieldMapping(
        object_id=mapped,
        object_name=FieldMatch("object_name", "Объект", "rich_text", "mapped", ("Объект",)),
        source=FieldMatch("source", "Source", "select", "mapped", ("Source",)),
        calendar_url=FieldMatch("calendar_url", "URL объекта календаря", "url", "mapped", ("URL объекта календаря",)),
        last_checked=FieldMatch("last_checked", "Last Checked", "date", "mapped", ("Last Checked",)),
        next_check=FieldMatch("next_check", "Next Check", "date", "mapped", ("Next Check",)),
        refresh_tier=FieldMatch("refresh_tier", "Refresh Tier", "select", "mapped", ("Refresh Tier",)),
        refresh_status=FieldMatch("refresh_status", "Refresh Status", "select", "mapped", ("Refresh Status",)),
        source_status=FieldMatch("source_status", "Source Status", "select", "mapped", ("Source Status",)),
        last_error=FieldMatch("last_error", "Last Error", "rich_text", "mapped", ("Last Error",)),
        month_columns=["Sep 26"],
    )


def test_sync_one_without_confirm_write_refused():
    with pytest.raises(SystemExit, match="REFUSED: sync-one requires --confirm-write"):
        run_sync_one("A_20260802_002", confirm_write=False)


def test_sync_one_other_object_refused():
    with pytest.raises(SystemExit, match="REFUSED: sync-one narrow write path"):
        run_sync_one("A_20260807_001", confirm_write=True)


def test_narrow_guard_blocks_other_object():
    writer = NotionWriter(_config(Path("/tmp/av-sync-test")))
    with pytest.raises(SyncOneWriteRefused):
        writer.sync_one_upsert(
            object_id="A_OTHER",
            object_name="x",
            source=SourceKind.AIRBNB,
            calendar_url="",
            month_cells={},
            last_checked=None,
            next_check=None,
            refresh_tier=RefreshTier.H12,
            refresh_status=RefreshStatus.SUCCESS,
            source_status=SourceStatus.ACTIVE,
            last_error="",
            target_schema=MagicMock(database_id="3bc2c251-5061-80c5-a13e-d394ec1a4eb1", properties={}),
            target_mapping=_target_mapping(),
        )


def test_sync_one_updates_existing_row(tmp_path: Path):
    config = _config(tmp_path)
    repo = AvailabilityRepository(config.sqlite_path)
    repo.upsert_property(
        PropertySource(
            object_id="A_20260802_002",
            name="Test",
            source=SourceKind.AIRBNB,
            source_url="",
        )
    )
    window = build_month_window(get_effective_window_start(date(2026, 8, 14), WindowStartMode.NEXT_MONTH))
    cal_start = date(window[0].year, window[0].month, 1)
    days = [
        calendar_day(cal_start, status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB),
        calendar_day(cal_start.replace(day=2), status=DailyAvailabilityStatus.AVAILABLE, source=SourceKind.AIRBNB),
    ]
    repo.upsert_calendar_days("A_20260802_002", days, fetched_at=datetime(2026, 8, 14, tzinfo=timezone.utc))
    repo.close()

    writer = NotionWriter(config)
    mapping = _target_mapping()
    schema_props = {
        "Object ID": {"type": "title"},
        "Объект": {"type": "rich_text"},
        "Source": {"type": "select"},
        "URL объекта календаря": {"type": "url"},
        "Sep 26": {"type": "rich_text"},
        "Last Checked": {"type": "date"},
        "Next Check": {"type": "date"},
        "Refresh Tier": {"type": "select"},
        "Refresh Status": {"type": "select"},
        "Source Status": {"type": "select"},
        "Last Error": {"type": "rich_text"},
    }
    page_id = "page-existing"
    calls: list[tuple[str, str]] = []

    def fake_request(method, path, payload=None):
        calls.append((method, path))
        if method == "POST" and path.endswith("/query"):
            return {"results": [{"id": page_id, "properties": {}}]}
        if method == "PATCH":
            return {"id": page_id}
        return {}

    writer._request = fake_request  # type: ignore[method-assign]

    action, pid = writer.sync_one_upsert(
        object_id="A_20260802_002",
        object_name="Test",
        source=SourceKind.AIRBNB,
        calendar_url="https://www.airbnb.com/rooms/1",
        month_cells={"Sep 26": "Доступен"},
        last_checked=datetime(2026, 8, 14, tzinfo=timezone.utc),
        next_check=datetime(2026, 8, 15, tzinfo=timezone.utc),
        refresh_tier=RefreshTier.H12,
        refresh_status=RefreshStatus.SUCCESS,
        source_status=SourceStatus.ACTIVE,
        last_error="",
        target_schema=MagicMock(
            database_id=config.target_database_id,
            properties=schema_props,
        ),
        target_mapping=mapping,
    )
    assert action == "updated"
    assert pid == page_id
    assert any(m == "PATCH" for m, _ in calls)
    assert writer.source_writes_performed == 0


def test_duplicate_object_id_blocked(tmp_path: Path):
    config = _config(tmp_path)
    writer = NotionWriter(config)

    def fake_request(method, path, payload=None):
        if method == "POST" and path.endswith("/query"):
            return {"results": [{"id": "a"}, {"id": "b"}]}
        return {}

    writer._request = fake_request  # type: ignore[method-assign]
    with pytest.raises(SyncOneWriteRefused, match="duplicate"):
        writer.sync_one_upsert(
            object_id="A_20260802_002",
            object_name="Test",
            source=SourceKind.AIRBNB,
            calendar_url="",
            month_cells={},
            last_checked=None,
            next_check=None,
            refresh_tier=RefreshTier.H12,
            refresh_status=RefreshStatus.SUCCESS,
            source_status=SourceStatus.ACTIVE,
            last_error="",
            target_schema=MagicMock(database_id=config.target_database_id, properties={}),
            target_mapping=_target_mapping(),
        )


def test_source_db_write_blocked(tmp_path: Path):
    config = _config(tmp_path)
    writer = NotionWriter(config)
    with pytest.raises(SyncOneWriteRefused, match="source «Аренда недвижимости»"):
        writer._narrow_guard("A_20260802_002", config.source_database_id)


def test_allowed_object_ids():
    assert "A_20260802_002" in SYNC_ONE_ALLOWED_OBJECT_IDS
