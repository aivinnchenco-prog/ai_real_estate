"""Tests for batch-safe gates and batch Notion upsert (no live network)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from availability_service.app.batch_safe import _validate_batch_request, run_batch_safe
from availability_service.app.config import AvailabilityConfig
from availability_service.app.models import (
    RefreshStatus,
    RefreshTier,
    SourceKind,
    TargetFieldMapping,
    WindowStartMode,
)

from availability_service.app.notion_reader import FieldMatch
from availability_service.app.notion_writer import NotionWriter, SyncOneWriteRefused


def _config(tmp_path: Path) -> AvailabilityConfig:
    return AvailabilityConfig(
        enabled=False,
        dry_run=True,
        notion_api_key="ntn_test",
        source_database_id="source-db",
        target_database_id="target-db",
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
        price_object_max_seconds=600,
    )


def _mapping() -> TargetFieldMapping:
    mapped = FieldMatch("x", "Object ID", "title", "mapped", ("Object ID",))
    return TargetFieldMapping(
        object_id=mapped,
        object_name=FieldMatch("object_name", "Объект", "rich_text", "mapped", ("Объект",)),
        source=FieldMatch("source", "Source", "select", "mapped", ("Source",)),
        calendar_url=FieldMatch(
            "calendar_url", "URL объекта календаря", "url", "mapped", ("URL объекта календаря",)
        ),
        last_checked=FieldMatch("last_checked", "Last Checked", "date", "mapped", ("Last Checked",)),
        next_check=FieldMatch("next_check", "Next Check", "date", "mapped", ("Next Check",)),
        refresh_tier=FieldMatch("refresh_tier", "Refresh Tier", "select", "mapped", ("Refresh Tier",)),
        refresh_status=FieldMatch("refresh_status", "Refresh Status", "select", "mapped", ("Refresh Status",)),
        source_status=FieldMatch("source_status", "Source Status", "select", "mapped", ("Source Status",)),
        last_error=FieldMatch("last_error", "Last Error", "rich_text", "mapped", ("Last Error",)),
        month_columns=["Sep 26"],
    )


def test_batch_safe_without_confirm_live_refused(tmp_path: Path):
    config = _config(tmp_path)
    with pytest.raises(SystemExit, match="REFUSED: batch-safe requires --confirm-live"):
        _validate_batch_request(
            ["A_20260802_002"],
            confirm_live=False,
            confirm_write=True,
            config=config,
        )


def test_batch_safe_without_confirm_write_refused(tmp_path: Path):
    config = _config(tmp_path)
    with pytest.raises(SystemExit, match="REFUSED: batch-safe requires --confirm-write"):
        _validate_batch_request(
            ["A_20260802_002"],
            confirm_live=True,
            confirm_write=False,
            config=config,
        )


def test_batch_safe_more_than_five_refused(tmp_path: Path):
    config = _config(tmp_path)
    ids = [f"A_{i:03d}" for i in range(6)]
    with pytest.raises(SystemExit, match="REFUSED: batch-safe max 5 objects"):
        _validate_batch_request(ids, confirm_live=True, confirm_write=True, config=config)


def test_batch_notion_upsert_updates_existing(tmp_path: Path):
    config = _config(tmp_path)
    writer = NotionWriter(config)
    mapping = _mapping()
    schema_props = {"Sep 26": {"type": "rich_text"}, "Object ID": {"type": "title"}}
    page_id = "page-1"
    calls: list[tuple[str, str]] = []

    def fake_request(method, path, payload=None):
        calls.append((method, path))
        if method == "POST" and path.endswith("/query"):
            return {"results": [{"id": page_id, "properties": {}}]}
        if method == "PATCH":
            return {"id": page_id}
        return {}

    writer._request = fake_request  # type: ignore[method-assign]
    batch_allowed = frozenset({"A_BATCH_001"})
    action, pid = writer.sync_batch_notion_upsert(
        object_id="A_BATCH_001",
        object_name="Batch Obj",
        source=SourceKind.AIRBNB,
        calendar_url="https://www.airbnb.com/rooms/1",
        batch_allowed_ids=batch_allowed,
        month_cells={"Sep 26": "Доступен"},
        last_checked=datetime(2026, 8, 14, tzinfo=timezone.utc),
        next_check=datetime(2026, 8, 15, tzinfo=timezone.utc),
        refresh_status=RefreshStatus.SUCCESS,
        last_error="",
        target_schema=MagicMock(database_id=config.target_database_id, properties=schema_props),
        target_mapping=mapping,
    )
    assert action == "updated"
    assert pid == page_id
    assert any(m == "PATCH" for m, _ in calls)
    assert not any(m == "POST" and "/pages" in p and not p.endswith("/query") for m, p in calls)


def test_batch_notion_upsert_creates_when_missing(tmp_path: Path):
    config = _config(tmp_path)
    writer = NotionWriter(config)
    mapping = _mapping()
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
    created_id = "page-new"
    calls: list[tuple[str, str]] = []

    def fake_request(method, path, payload=None):
        calls.append((method, path))
        if method == "POST" and path.endswith("/query"):
            return {"results": []}
        if method == "POST" and path == "/pages":
            return {"id": created_id}
        return {}

    writer._request = fake_request  # type: ignore[method-assign]
    batch_allowed = frozenset({"A_BATCH_NEW"})
    action, pid = writer.sync_batch_notion_upsert(
        object_id="A_BATCH_NEW",
        object_name="New Batch Obj",
        source=SourceKind.AIRBNB,
        calendar_url="https://www.airbnb.com/rooms/2",
        batch_allowed_ids=batch_allowed,
        month_cells={"Sep 26": "Доступен"},
        last_checked=datetime(2026, 8, 14, tzinfo=timezone.utc),
        next_check=datetime(2026, 8, 15, tzinfo=timezone.utc),
        refresh_status=RefreshStatus.SUCCESS,
        last_error="",
        target_schema=MagicMock(database_id=config.target_database_id, properties=schema_props),
        target_mapping=mapping,
    )
    assert action == "created"
    assert pid == created_id
    assert any(m == "POST" and p == "/pages" for m, p in calls)


def test_batch_notion_upsert_duplicate_refused(tmp_path: Path):
    config = _config(tmp_path)
    writer = NotionWriter(config)

    def fake_request(method, path, payload=None):
        if method == "POST" and path.endswith("/query"):
            return {"results": [{"id": "a"}, {"id": "b"}]}
        return {}

    writer._request = fake_request  # type: ignore[method-assign]
    with pytest.raises(SyncOneWriteRefused, match="duplicate"):
        writer.sync_batch_notion_upsert(
            object_id="A_BATCH_DUP",
            object_name="Dup",
            source=SourceKind.AIRBNB,
            calendar_url="",
            batch_allowed_ids=frozenset({"A_BATCH_DUP"}),
            month_cells={},
            last_checked=None,
            next_check=None,
            refresh_status=RefreshStatus.SUCCESS,
            last_error="",
            target_schema=MagicMock(database_id=config.target_database_id, properties={}),
            target_mapping=_mapping(),
        )


def test_batch_notion_upsert_outside_allowlist_refused(tmp_path: Path):
    config = _config(tmp_path)
    writer = NotionWriter(config)
    with pytest.raises(SyncOneWriteRefused):
        writer.sync_batch_notion_upsert(
            object_id="A_NOT_IN_BATCH",
            object_name="x",
            source=SourceKind.AIRBNB,
            calendar_url="",
            batch_allowed_ids=frozenset({"A_OTHER"}),
            month_cells={},
            last_checked=None,
            next_check=None,
            refresh_status=RefreshStatus.SUCCESS,
            last_error="",
            target_schema=MagicMock(database_id=config.target_database_id, properties={}),
            target_mapping=_mapping(),
        )


def test_run_batch_safe_cli_refuses_without_flags():
    with pytest.raises(SystemExit, match="REFUSED: batch-safe requires --confirm-live"):
        run_batch_safe(["A_20260802_002"], confirm_live=False, confirm_write=False)
