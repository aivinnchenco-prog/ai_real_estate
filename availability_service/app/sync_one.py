"""Controlled one-object sync from SQLite → Notion target «Доступность»."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .config import AvailabilityConfig, load_config
from .models import (
    MonthAvailability,
    PropertySource,
    RefreshStatus,
    RefreshTier,
    SourceKind,
    SourceStatus,
    TIER_HOURS,
    get_availability_window,
    get_effective_window_start,
    window_calendar_bounds,
)
from .month_display import (
    build_month_display_rows,
    month_display_counts,
)
from .notion_reader import NotionReader, compare_month_schema, map_source_fields, map_target_fields
from .notion_writer import NotionWriter
from .repository import AvailabilityRepository
from .target_column_order import LOCKED_MONTH_COLUMNS, LOCKED_TECHNICAL_COLUMNS
from .status_mapper import build_month_availabilities, normalize_calendar
from .status_mapper import AirbnbMonthPolicy

SYNC_ONE_ALLOWED_OBJECT_IDS = frozenset({"A_20260802_002"})


class SourcePropertyMissingError(Exception):
    """Object exists in availability SQLite but was removed from source Notion DB."""

    def __init__(self, object_id: str) -> None:
        self.object_id = object_id
        super().__init__(
            f"object {object_id} not found in source «Аренда недвижимости»"
        )


@dataclass
class SyncOneResult:
    object_id: str = ""
    name: str = ""
    source: str = ""
    calendar_url: str = ""
    notion_action: str = ""  # created | updated
    notion_page_id: str = ""
    month_cells: dict[str, str] = field(default_factory=dict)
    month_display_counts: dict[str, int] = field(default_factory=dict)
    last_checked: str = ""
    next_check: str = ""
    refresh_tier: str = ""
    refresh_status: str = ""
    source_status: str = ""
    calendar_days_count: int = 0
    monthly_rows_count: int = 0
    integrity_check: str = ""
    notion_rows_for_object: int = 0
    notion_readback_pass: bool = False
    readback: dict[str, str] = field(default_factory=dict)
    source_writes: int = 0
    other_rows_changed: int = 0
    error: str = ""


def _validate_sync_request(object_id: str, confirm_write: bool) -> None:
    if not object_id or not object_id.strip():
        raise SystemExit("REFUSED: --object-id is required")
    if not confirm_write:
        raise SystemExit("REFUSED: sync-one requires --confirm-write")
    oid = object_id.strip()
    if oid not in SYNC_ONE_ALLOWED_OBJECT_IDS:
        raise SystemExit(
            f"REFUSED: sync-one narrow write path allows only: {sorted(SYNC_ONE_ALLOWED_OBJECT_IDS)}"
        )


def _find_source_property(
    reader: NotionReader,
    config: AvailabilityConfig,
    object_id: str,
    *,
    expected_source: SourceKind | None = SourceKind.AIRBNB,
) -> PropertySource:
    schema = reader.fetch_schema(config.source_database_id)
    if schema.error:
        raise SystemExit(f"STOP: source schema error: {schema.error}")
    mapping = map_source_fields(schema.properties)
    if mapping.object_id.status != "mapped" or not mapping.object_id.property_name:
        raise SystemExit("STOP: Object ID column not mapped in source schema")

    prop_name = mapping.object_id.property_name
    prop_meta = schema.properties.get(prop_name) or {}
    prop_type = str(prop_meta.get("type") or "rich_text")
    if prop_type == "title":
        text_filter = {"title": {"equals": object_id}}
    else:
        text_filter = {"rich_text": {"equals": object_id}}

    data = reader._request(
        "POST",
        f"/databases/{config.source_database_id}/query",
        {
            "filter": {"property": prop_name, **text_filter},
            "page_size": 5,
        },
    )
    results = data.get("results") or []
    if not results:
        raise SourcePropertyMissingError(object_id)

    from .notion_reader import _plain_property, resolve_calendar_check_url

    page = results[0]
    props = page.get("properties") or {}
    oid = _plain_property(props.get(prop_name))
    name = _plain_property(
        props.get(mapping.name.property_name) if mapping.name and mapping.name.property_name else None
    )
    source_url = ""
    if mapping.source_url and mapping.source_url.property_name:
        source_url = _plain_property(props.get(mapping.source_url.property_name))
    raw_calendar_url = ""
    if mapping.calendar_url and mapping.calendar_url.property_name:
        raw_calendar_url = _plain_property(props.get(mapping.calendar_url.property_name))
    raw_source = ""
    if mapping.source and mapping.source.property_name:
        raw_source = _plain_property(props.get(mapping.source.property_name))

    from .notion_reader import normalize_source

    source = normalize_source(raw_source=raw_source, source_url=source_url, object_id=oid)
    if expected_source is not None and source != expected_source:
        raise SystemExit(
            f"STOP: source for {object_id} is {source.value}, expected {expected_source.value}"
        )

    calendar_url = resolve_calendar_check_url(
        calendar_url=raw_calendar_url,
        source_url=source_url,
        source=source,
    )

    return PropertySource(
        object_id=oid.strip(),
        name=name.strip(),
        source=source,
        source_url=source_url.strip(),
        notion_page_id=page.get("id") or "",
        calendar_url=calendar_url,
    )


def _calendar_range_for_window(window: list) -> tuple[date, date]:
    start, end = window_calendar_bounds(window)
    # get_calendar_days uses [date_from, date_to) — end is exclusive
    return start, end + timedelta(days=1)


def _max_calendar_fetched_at(repo: AvailabilityRepository, object_id: str) -> datetime | None:
    row = repo._conn.execute(
        "SELECT MAX(fetched_at) AS mx FROM availability_calendar_days WHERE object_id = ?",
        (object_id,),
    ).fetchone()
    if not row or not row["mx"]:
        return None
    return datetime.fromisoformat(row["mx"])


def _ensure_monthly_from_calendar(
    repo: AvailabilityRepository,
    object_id: str,
    window: list,
    calendar: dict[date, bool],
    fetched_at: datetime,
) -> int:
    """Persist calendar-derived month rows when no monthly data exists (no fabricated prices)."""
    if repo.count_monthly_rows(object_id) > 0:
        return 0

    from .month_display import evaluate_month_display_status

    months: list[MonthAvailability] = []
    display_statuses: dict[str, str] = {}
    for item in window:
        display = evaluate_month_display_status(calendar, item.year, item.month)
        month_key = item.key
        display_statuses[month_key] = display.value
        months.append(
            MonthAvailability(
                year=item.year,
                month=item.month,
                price=None,
            )
        )
    return repo.replace_monthly_rows(
        object_id, months, fetched_at=fetched_at, display_statuses=display_statuses
    )


def _restore_monthly_from_probe_snapshot(
    repo: AvailabilityRepository,
    object_id: str,
    window: list,
    calendar: dict[date, bool],
    fetched_at: datetime,
) -> int:
    if repo.count_monthly_rows(object_id) > 0:
        return 0
    raw = repo.get_state(f"probe_snapshot:{object_id}")
    if not raw:
        return 0
    try:
        snapshot = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    price_entries = snapshot.get("price_entries") or {}
    month_rows = build_month_availabilities(
        window,
        calendar,
        price_entries,
        AirbnbMonthPolicy.FULL_MONTH_REQUIRED,
    )
    display_statuses: dict[str, str] = {}
    for item in window:
        from .month_display import evaluate_month_display_status

        display = evaluate_month_display_status(calendar, item.year, item.month)
        display_statuses[item.key] = display.value
    return repo.replace_monthly_rows(
        object_id, month_rows, fetched_at=fetched_at, display_statuses=display_statuses
    )


def _derive_source_status(calendar_count: int, last_checked: datetime | None) -> SourceStatus:
    if calendar_count > 0 and last_checked is not None:
        return SourceStatus.ACTIVE
    return SourceStatus.UNKNOWN


def run_sync_one(object_id: str, *, confirm_write: bool = False) -> SyncOneResult:
    _validate_sync_request(object_id, confirm_write)
    object_id = object_id.strip()
    config = load_config()
    if not config.notion_api_key:
        raise SystemExit("NOTION_API_KEY / NOTION_TOKEN is missing in .env")
    if not config.source_database_id or not config.target_database_id:
        raise SystemExit("Source/target Notion database IDs are missing in .env")

    result = SyncOneResult(object_id=object_id)
    reader = NotionReader(config)
    writer = NotionWriter(config)

    prop = _find_source_property(reader, config, object_id)
    result.name = prop.name
    result.source = prop.source.value
    result.calendar_url = prop.calendar_url

    target_schema = reader.fetch_schema(config.target_database_id)
    if target_schema.error:
        raise SystemExit(f"STOP: target schema error: {target_schema.error}")
    target_mapping = map_target_fields(target_schema.properties)

    today = date.today()
    window = get_availability_window(config.window_start_mode, now=today)
    matched, msg = compare_month_schema(window, target_mapping.month_columns)
    if not matched:
        raise SystemExit(f"STOP: MONTH_SCHEMA_MATCH {msg}")

    repo = AvailabilityRepository(config.sqlite_path)
    try:
        calendar_count = repo.count_calendar_days(object_id)
        if calendar_count == 0:
            raise SystemExit(
                f"STOP: no calendar data for {object_id} in {config.sqlite_path}. "
                "Run probe-airbnb first."
            )

        cal_start, cal_end = _calendar_range_for_window(window)
        calendar_days = repo.get_calendar_days(object_id, cal_start, cal_end)
        calendar = {day.date: day.available for day in calendar_days}
        result.calendar_days_count = repo.count_calendar_days(object_id)

        last_checked = _max_calendar_fetched_at(repo, object_id)
        if last_checked is None:
            state = repo.get_object(object_id)
            last_checked = state.last_checked_at if state else None

        fetched_at = last_checked or datetime.now(timezone.utc)
        restored = _restore_monthly_from_probe_snapshot(
            repo, object_id, window, calendar, fetched_at
        )
        ensured = _ensure_monthly_from_calendar(
            repo, object_id, window, calendar, fetched_at
        )
        if restored == 0 and ensured == 0 and repo.count_monthly_rows(object_id) == 0:
            _ensure_monthly_from_calendar(repo, object_id, window, calendar, fetched_at)

        monthly_rows = repo.get_monthly_rows(object_id)
        result.monthly_rows_count = len(monthly_rows)

        price_map: dict[str, float | None] = {}
        for row in monthly_rows:
            price_map[row.month_key] = row.price

        display_result = build_month_display_rows(window, calendar, price_map)
        result.month_cells = {
            item.display_name: cell for item, _, cell in display_result.rows
        }
        result.month_display_counts = month_display_counts(display_result.rows)

        tier = RefreshTier.H12
        refresh_status = RefreshStatus.SUCCESS
        source_status = _derive_source_status(calendar_count, last_checked)
        next_check = None
        if last_checked:
            next_check = last_checked + timedelta(hours=TIER_HOURS[tier])

        result.last_checked = last_checked.isoformat() if last_checked else ""
        result.next_check = next_check.isoformat() if next_check else ""
        result.refresh_tier = tier.value
        result.refresh_status = refresh_status.value
        result.source_status = source_status.value

        integrity = repo._conn.execute("PRAGMA integrity_check").fetchone()
        result.integrity_check = integrity[0] if integrity else "fail"

        action, page_id = writer.sync_one_upsert(
            object_id=object_id,
            object_name=prop.name,
            source=prop.source,
            calendar_url=prop.calendar_url,
            month_cells=result.month_cells,
            last_checked=last_checked,
            next_check=next_check,
            refresh_tier=tier,
            refresh_status=refresh_status,
            source_status=source_status,
            last_error="",
            target_schema=target_schema,
            target_mapping=target_mapping,
        )
        result.notion_action = action
        result.notion_page_id = page_id
        result.source_writes = writer.source_writes_performed

        readback = writer.read_target_row(
            config.target_database_id,
            target_mapping,
            object_id,
            target_schema.properties,
        )
        result.readback = readback
        result.notion_rows_for_object = writer.count_target_rows_by_object_id(
            config.target_database_id,
            target_mapping,
            object_id,
            target_schema.properties,
        )
        result.notion_readback_pass = _verify_readback(result, readback)
    finally:
        repo.close()

    return result


def _verify_readback(result: SyncOneResult, readback: dict[str, str]) -> bool:
    if result.notion_rows_for_object != 1:
        return False
    if readback.get("Object ID") != result.object_id:
        return False
    if readback.get("Объект") != result.name:
        return False
    if readback.get("Source") != result.source:
        return False
    if result.calendar_url and readback.get("URL объекта календаря") != result.calendar_url:
        return False
    for month_name, expected in result.month_cells.items():
        if readback.get(month_name) != expected:
            return False
    if readback.get("Refresh Tier") != result.refresh_tier:
        return False
    if readback.get("Refresh Status") != result.refresh_status:
        return False
    if readback.get("Source Status") != result.source_status:
        return False
    if readback.get("Last Error"):
        return False
    if result.last_checked and not readback.get("Last Checked"):
        return False
    if result.next_check and not readback.get("Next Check"):
        return False
    return True


def print_sync_one_report(result: SyncOneResult) -> None:
    print("\n== OBJECT ==")
    print(f"  Object ID: {result.object_id}")
    print(f"  Name: {result.name}")
    if result.calendar_url:
        print(f"  Calendar URL: {result.calendar_url}")

    print("\n== NOTION ROW ==")
    print(f"  action: {result.notion_action}")
    print(f"  page_id: {result.notion_page_id}")

    print("\n== MONTHS ==")
    month_order = list(LOCKED_MONTH_COLUMNS)
    for name in month_order:
        print(f"  {name} → {result.month_cells.get(name, '—')}")

    print("\n== MONTH DISPLAY SUMMARY ==")
    print(f"  fully_available: {result.month_display_counts.get('fully_available', 0)}")
    print(f"  partial: {result.month_display_counts.get('partial', 0)}")
    print(f"  unavailable: {result.month_display_counts.get('unavailable', 0)}")
    print(f"  unknown: {result.month_display_counts.get('unknown', 0)}")

    print("\n== SQLITE ==")
    print(f"  calendar days count: {result.calendar_days_count}")
    print(f"  monthly rows count: {result.monthly_rows_count}")
    print(f"  integrity_check: {result.integrity_check}")

    print("\n== NOTION ==")
    print(f"  rows for {result.object_id} = {result.notion_rows_for_object}")
    rb = "PASS" if result.notion_readback_pass else "FAIL"
    print(f"  NOTION_READBACK = {rb}")

    if result.readback:
        print("\n== NOTION READBACK ==")
        technical_order = list(LOCKED_TECHNICAL_COLUMNS)
        readback_keys = list(month_order) + [
            k for k in technical_order if k in result.readback
        ]
        for key in readback_keys:
            print(f"  {key}: {result.readback[key]}")

    print("\n== SAFETY ==")
    print(f"  source «Аренда недвижимости» writes: {result.source_writes}")
    print(f"  other availability rows changed: {result.other_rows_changed}")
    print("  Agent1 unchanged")
    print("  Agent2 unchanged")
    print("  scheduler OFF")
    print("  systemd unchanged")
    print("  website unchanged")
    print("  background LIVE OFF")
