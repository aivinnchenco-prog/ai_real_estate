from pathlib import Path

from availability_service.app.config import AvailabilityConfig
from availability_service.app.models import RefreshTier, WindowStartMode
from availability_service.app.notion_writer import NotionWriteBlocked, NotionWriter


def _config(*, enabled: bool, dry_run: bool) -> AvailabilityConfig:
    runtime = Path("/tmp/availability-test")
    return AvailabilityConfig(
        enabled=enabled,
        dry_run=dry_run,
        notion_api_key="ntn_test",
        source_database_id="source",
        target_database_id="target",
        target_data_source_id="",
        runtime_dir=runtime,
        sqlite_path=runtime / "availability.sqlite3",
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


def test_dry_run_blocks_notion_writes():
    writer = NotionWriter(_config(enabled=True, dry_run=True))
    try:
        writer.upsert_availability_row(None, [])  # type: ignore[arg-type]
        assert False, "expected NotionWriteBlocked"
    except NotionWriteBlocked as exc:
        assert "AVAILABILITY_DRY_RUN=true" in str(exc)
    assert writer.writes_performed == 0


def test_disabled_blocks_notion_writes():
    writer = NotionWriter(_config(enabled=False, dry_run=False))
    try:
        writer.create_properties({"Sep 26": {"rich_text": {}}})
        assert False, "expected NotionWriteBlocked"
    except NotionWriteBlocked as exc:
        assert "AVAILABILITY_ENABLED=false" in str(exc)
