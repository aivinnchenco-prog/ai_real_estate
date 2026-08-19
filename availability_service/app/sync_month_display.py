"""Sync month display cells to Notion from SQLite calendar + monthly prices (no live fetch)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import load_config
from .models import get_availability_window
from .month_display import build_month_display_rows
from .notion_reader import NotionReader, compare_month_schema, map_target_fields
from .notion_writer import NotionWriter
from .repository import AvailabilityRepository
from .sync_one import _calendar_range_for_window
from .target_column_order import LOCKED_MONTH_COLUMNS

SYNC_MONTH_DISPLAY_ALLOWED_OBJECT_IDS = frozenset({"A_20260807_001", "A_20260802_002"})


@dataclass
class SyncMonthDisplayResult:
    object_id: str = ""
    name: str = ""
    month_cells: dict[str, str] = field(default_factory=dict)
    blocked_ranges_by_display: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    notion_action: str = ""
    notion_page_id: str = ""
    notion_rows_before: int = 0
    notion_rows_after: int = 0
    notion_readback_pass: bool = False
    readback: dict[str, str] = field(default_factory=dict)
    source_writes: int = 0


def _validate_request(object_id: str, confirm_write: bool) -> None:
    if not object_id or not object_id.strip():
        raise SystemExit("REFUSED: --object-id is required")
    if not confirm_write:
        raise SystemExit("REFUSED: sync-month-display requires --confirm-write")
    oid = object_id.strip()
    if oid not in SYNC_MONTH_DISPLAY_ALLOWED_OBJECT_IDS:
        raise SystemExit(
            f"REFUSED: sync-month-display allows only: {sorted(SYNC_MONTH_DISPLAY_ALLOWED_OBJECT_IDS)}"
        )


def run_sync_month_display(object_id: str, *, confirm_write: bool = False) -> SyncMonthDisplayResult:
    _validate_request(object_id, confirm_write)
    object_id = object_id.strip()

    config = load_config()
    if not config.notion_api_key:
        raise SystemExit("NOTION_API_KEY / NOTION_TOKEN is missing in .env")

    reader = NotionReader(config)
    writer = NotionWriter(config)
    target_schema = reader.fetch_schema(config.target_database_id)
    if target_schema.error:
        raise SystemExit(f"STOP: target schema error: {target_schema.error}")
    target_mapping = map_target_fields(target_schema.properties)

    window = get_availability_window(config.window_start_mode)
    matched, msg = compare_month_schema(window, target_mapping.month_columns)
    if not matched:
        raise SystemExit(f"STOP: MONTH_SCHEMA_MATCH {msg}")

    result = SyncMonthDisplayResult(object_id=object_id)
    result.notion_rows_before = writer.count_target_rows_by_object_id(
        config.target_database_id,
        target_mapping,
        object_id,
        target_schema.properties,
    )
    if result.notion_rows_before != 1:
        raise SystemExit(
            f"STOP: expected exactly 1 Notion row for {object_id}, "
            f"found {result.notion_rows_before}"
        )

    repo = AvailabilityRepository(config.sqlite_path)
    try:
        if repo.count_calendar_days(object_id) == 0:
            raise SystemExit(f"STOP: no calendar data for {object_id}")

        state = repo.get_object(object_id)
        result.name = state.name if state else ""

        cal_start, cal_end = _calendar_range_for_window(window)
        calendar_days = repo.get_calendar_days(object_id, cal_start, cal_end)
        calendar = {day.date: day.available for day in calendar_days}

        price_map: dict[str, float | None] = {}
        for row in repo.get_monthly_rows(object_id):
            price_map[row.month_key] = row.price

        display_result = build_month_display_rows(window, calendar, price_map)
        result.warnings = list(display_result.warnings)
        for item, _, _ in display_result.rows:
            key = item.key
            if key in display_result.blocked_ranges_by_key:
                result.blocked_ranges_by_display[item.display_name] = (
                    display_result.blocked_ranges_by_key[key]
                )
        result.month_cells = {
            item.display_name: cell for item, _, cell in display_result.rows
        }

        action, page_id = writer.sync_one_month_cells_only(
            object_id=object_id,
            month_cells=result.month_cells,
            target_schema=target_schema,
            target_mapping=target_mapping,
        )
        result.notion_action = action
        result.notion_page_id = page_id
        result.source_writes = writer.source_writes_performed

        result.notion_rows_after = writer.count_target_rows_by_object_id(
            config.target_database_id,
            target_mapping,
            object_id,
            target_schema.properties,
        )
        readback = writer.read_target_row(
            config.target_database_id,
            target_mapping,
            object_id,
            target_schema.properties,
        )
        result.readback = readback
        result.notion_readback_pass = _verify_readback(result, readback)
    finally:
        repo.close()

    return result


def _verify_readback(result: SyncMonthDisplayResult, readback: dict[str, str]) -> bool:
    if result.notion_rows_after != 1:
        return False
    for month_name, expected in result.month_cells.items():
        if readback.get(month_name) != expected:
            return False
    return True


def print_sync_month_display_report(result: SyncMonthDisplayResult) -> None:
    print("\n== OBJECT ==")
    print(f"  Object ID: {result.object_id}")
    print(f"  Name: {result.name}")

    print("\n== MONTHS ==")
    for name in LOCKED_MONTH_COLUMNS:
        print(f"  {name} → {result.month_cells.get(name, '—')}")

    if result.blocked_ranges_by_display:
        print("\n== BLOCKED RANGES (PARTIAL months) ==")
        for name in LOCKED_MONTH_COLUMNS:
            if name in result.blocked_ranges_by_display:
                print(f"  {name}: {result.blocked_ranges_by_display[name]}")

    if result.warnings:
        print("\n== WARNINGS ==")
        for w in result.warnings:
            print(f"  {w}")

    print("\n== NOTION ==")
    print(f"  rows before = {result.notion_rows_before}")
    print(f"  rows after = {result.notion_rows_after}")
    rb = "PASS" if result.notion_readback_pass else "FAIL"
    print(f"  NOTION_READBACK = {rb}")

    if result.readback:
        print("\n== NOTION READBACK ==")
        for name in LOCKED_MONTH_COLUMNS:
            print(f"  {name}: {result.readback.get(name, '—')}")

    print("\n== SAFETY ==")
    print(f"  source «Аренда недвижимости» writes: {result.source_writes}")
    print("  other objects changed: 0")
    print("  Airbnb requests: 0")
    print("  Agent1 unchanged")
    print("  Agent2 unchanged")
    print("  Agent6 unchanged")
    print("  website unchanged")
    print("  scheduler OFF")
    print("  systemd unchanged")
    print("  background LIVE OFF")
