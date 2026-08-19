"""Tests for refresh-one-airbnb safety gates."""
from __future__ import annotations

from pathlib import Path

import pytest

from availability_service.app.config import AvailabilityConfig
from availability_service.app.models import RefreshStatus, RefreshTier, WindowStartMode
from availability_service.app.notion_writer import NotionWriter, SyncOneWriteRefused
from availability_service.app.refresh_one_airbnb import (
    REFRESH_ONE_ALLOWED_OBJECT_IDS,
    run_refresh_one_airbnb,
)


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


def test_refresh_without_confirm_live_refused():
    with pytest.raises(SystemExit, match="REFUSED: refresh-one-airbnb requires --confirm-live"):
        run_refresh_one_airbnb(
            "A_20260807_001",
            pricing=True,
            sync_notion=True,
            confirm_live=False,
            confirm_write=True,
        )


def test_refresh_without_confirm_write_refused():
    with pytest.raises(SystemExit, match="REFUSED: refresh-one-airbnb requires --confirm-write"):
        run_refresh_one_airbnb(
            "A_20260807_001",
            pricing=True,
            sync_notion=True,
            confirm_live=True,
            confirm_write=False,
        )


def test_refresh_other_object_refused():
    with pytest.raises(SystemExit, match="REFUSED: refresh-one narrow path"):
        run_refresh_one_airbnb(
            "A_20260802_002",
            pricing=True,
            sync_notion=True,
            confirm_live=True,
            confirm_write=True,
        )


def test_refresh_pricing_refresh_blocks_wrong_object(tmp_path: Path):
    writer = NotionWriter(_config(tmp_path))
    from unittest.mock import MagicMock

    from availability_service.app.models import TargetFieldMapping
    from availability_service.app.notion_reader import FieldMatch

    mapping = TargetFieldMapping(
        object_id=FieldMatch("object_id", "Object ID", "title", "mapped", ("Object ID",)),
        month_columns=["Sep 26"],
    )
    with pytest.raises(SyncOneWriteRefused, match="not in allowlist"):
        writer.sync_one_pricing_refresh(
            object_id="A_OTHER",
            month_cells={"Sep 26": "Доступен"},
            last_checked=None,
            next_check=None,
            refresh_status=RefreshStatus.SUCCESS,
            last_error="",
            target_schema=MagicMock(database_id="3bc2c251-5061-80c5-a13e-d394ec1a4eb1", properties={}),
            target_mapping=mapping,
        )


def test_allowed_object_ids():
    assert "A_20260807_001" in REFRESH_ONE_ALLOWED_OBJECT_IDS
