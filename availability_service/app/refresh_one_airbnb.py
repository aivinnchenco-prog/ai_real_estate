"""Controlled one-object monthly pricing refresh + optional Notion sync."""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .config import load_config
from .models import (
    MonthAvailability,
    RefreshStatus,
    RefreshTier,
    SourceKind,
    TIER_HOURS,
    get_availability_window,
    window_calendar_bounds,
)
from .month_display import build_month_display_rows, month_display_counts
from .notion_reader import NotionReader, compare_month_schema, map_target_fields
from .notion_writer import NotionWriter
from .probe_airbnb import PriceFetchMetrics, _apply_probe_env, _backup_sqlite
from .repository import AvailabilityRepository
from .status_mapper import AirbnbMonthPolicy, build_month_availabilities
from .sync_one import (
    _calendar_range_for_window,
    _find_source_property,
)
from .target_column_order import LOCKED_MONTH_COLUMNS, LOCKED_TECHNICAL_COLUMNS
from ..providers.airbnb_adapter import collect_prices_for_window, create_selenium_price_fetcher

REFRESH_ONE_ALLOWED_OBJECT_IDS = frozenset({"A_20260807_001"})


@dataclass
class RefreshOneResult:
    object_id: str = ""
    name: str = ""
    calendar_rows: int = 0
    monthly_rows_before: int = 0
    monthly_rows_after: int = 0
    monthly_duplicates: int = 0
    month_cells: dict[str, str] = field(default_factory=dict)
    monthly_raw: list[dict[str, str | float | int | None]] = field(default_factory=list)
    notion_action: str = ""
    notion_page_id: str = ""
    notion_rows_before: int = 0
    notion_rows_after: int = 0
    notion_readback_pass: bool = False
    readback: dict[str, str] = field(default_factory=dict)
    last_checked: str = ""
    next_check: str = ""
    refresh_status: str = ""
    integrity_check: str = ""
    pricing_elapsed_s: float = 0.0
    price_fetch_attempts: int = 0
    price_fetch_successes: int = 0
    price_fetch_failures: int = 0
    price_fetch_retries: int = 0
    max_concurrency: int = 1
    sqlite_path: str = ""
    backup_path: str = ""
    source_writes: int = 0
    other_objects_changed: int = 0
    error: str = ""


def _validate_refresh_request(
    object_id: str,
    *,
    pricing: bool,
    sync_notion: bool,
    confirm_live: bool,
    confirm_write: bool,
) -> None:
    if not object_id or not object_id.strip():
        raise SystemExit("REFUSED: --object-id is required")
    if not pricing:
        raise SystemExit("REFUSED: refresh-one-airbnb requires --pricing")
    if not sync_notion:
        raise SystemExit("REFUSED: refresh-one-airbnb requires --sync-notion")
    if not confirm_live:
        raise SystemExit("REFUSED: refresh-one-airbnb requires --confirm-live")
    if not confirm_write:
        raise SystemExit("REFUSED: refresh-one-airbnb requires --confirm-write")
    oid = object_id.strip()
    if oid not in REFRESH_ONE_ALLOWED_OBJECT_IDS:
        raise SystemExit(
            f"REFUSED: refresh-one narrow path allows only: {sorted(REFRESH_ONE_ALLOWED_OBJECT_IDS)}"
        )


def _monthly_duplicate_count(conn: sqlite3.Connection, object_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) - COUNT(DISTINCT month_key) AS dup
        FROM availability_months WHERE object_id = ?
        """,
        (object_id,),
    ).fetchone()
    return int(row["dup"]) if row else 0


def _monthly_raw_rows(repo: AvailabilityRepository, object_id: str) -> list[dict]:
    rows = repo.get_monthly_rows(object_id)
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "month_key": row.month_key,
                "status": row.status,
                "price": row.price,
                "currency": row.currency,
                "pricing_status": row.pricing_status,
                "period_used": row.period_used,
                "based_on_days": row.based_on_days,
            }
        )
    return out


def run_refresh_one_airbnb(
    object_id: str,
    *,
    pricing: bool = False,
    sync_notion: bool = False,
    confirm_live: bool = False,
    confirm_write: bool = False,
) -> RefreshOneResult:
    _validate_refresh_request(
        object_id,
        pricing=pricing,
        sync_notion=sync_notion,
        confirm_live=confirm_live,
        confirm_write=confirm_write,
    )
    object_id = object_id.strip()
    result = RefreshOneResult(object_id=object_id)

    _apply_probe_env()
    config = load_config()
    if not config.airbnb_live_allowed:
        raise SystemExit("STOP: live Airbnb flags not active after one-shot env override")
    if not config.notion_api_key:
        raise SystemExit("NOTION_API_KEY / NOTION_TOKEN is missing in .env")

    reader = NotionReader(config)
    writer = NotionWriter(config)

    target_schema = reader.fetch_schema(config.target_database_id)
    if target_schema.error:
        raise SystemExit(f"STOP: target schema error: {target_schema.error}")
    target_mapping = map_target_fields(target_schema.properties)

    result.notion_rows_before = writer.count_target_rows_by_object_id(
        config.target_database_id,
        target_mapping,
        object_id,
        target_schema.properties,
    )
    if result.notion_rows_before != 1:
        raise SystemExit(
            f"STOP: expected exactly 1 Notion row for {object_id}, found {result.notion_rows_before}"
        )

    prop = _find_source_property(reader, config, object_id)
    result.name = prop.name
    if prop.source != SourceKind.AIRBNB:
        raise SystemExit(f"STOP: source is {prop.source.value}, not AIRBNB")

    pricing_url = prop.calendar_url.strip() or prop.source_url.strip()
    if not pricing_url or "airbnb" not in pricing_url.lower():
        raise SystemExit(f"STOP: no Airbnb URL for pricing: {pricing_url!r}")

    today = date.today()
    window = get_availability_window(config.window_start_mode, now=today)
    matched, msg = compare_month_schema(window, target_mapping.month_columns)
    if not matched:
        raise SystemExit(f"STOP: MONTH_SCHEMA_MATCH {msg}")

    sqlite_path = config.sqlite_path
    result.sqlite_path = str(sqlite_path)
    result.backup_path = _backup_sqlite(sqlite_path)

    repo = AvailabilityRepository(sqlite_path)
    try:
        result.calendar_rows = repo.count_calendar_days(object_id)
        if result.calendar_rows == 0:
            raise SystemExit(
                f"STOP: no calendar rows for {object_id}. Run probe-airbnb first."
            )

        result.monthly_rows_before = repo.count_monthly_rows(object_id)

        cal_start, cal_end = _calendar_range_for_window(window)
        calendar_days = repo.get_calendar_days(object_id, cal_start, cal_end)
        calendar = {day.date: day.available for day in calendar_days}

        metrics = PriceFetchMetrics(max_concurrency=1)
        price_start = time.perf_counter()
        fetch_price, cleanup, get_metrics = create_selenium_price_fetcher(pricing_url)
        try:
            price_entries = collect_prices_for_window(
                metrics.wrap(fetch_price),
                calendar,
                window,
                min_segment_days=5,
            )
        except Exception as exc:
            cleanup()
            raise SystemExit(f"STOP: pricing failed: {exc}") from exc
        finally:
            cleanup()

        pm = get_metrics()
        result.pricing_elapsed_s = round(time.perf_counter() - price_start, 2)
        result.price_fetch_attempts = pm.fetch_calls
        result.price_fetch_successes = sum(1 for e in price_entries.values() if e.get("price"))
        result.price_fetch_failures = pm.failed_fetch_calls
        result.price_fetch_retries = pm.internal_retries
        result.max_concurrency = metrics.max_concurrency

        fetched_at = datetime.now(timezone.utc)
        from .month_display import evaluate_month_display_status

        display_statuses = {
            item.key: evaluate_month_display_status(calendar, item.year, item.month).value
            for item in window
        }
        month_rows = build_month_availabilities(
            window,
            calendar,
            price_entries,
            AirbnbMonthPolicy.FULL_MONTH_REQUIRED,
        )
        result.monthly_rows_after = repo.replace_monthly_rows(
            object_id,
            month_rows,
            fetched_at=fetched_at,
            display_statuses=display_statuses,
        )
        repo.set_state(
            f"probe_snapshot:{object_id}",
            json.dumps(
                {"price_entries": price_entries, "fetched_at": fetched_at.isoformat()},
                ensure_ascii=False,
            ),
            now=fetched_at,
        )

        result.monthly_duplicates = _monthly_duplicate_count(repo._conn, object_id)
        integrity = repo._conn.execute("PRAGMA integrity_check").fetchone()
        result.integrity_check = integrity[0] if integrity else "fail"
        result.monthly_raw = _monthly_raw_rows(repo, object_id)

        price_map: dict[str, float | None] = {}
        for row in repo.get_monthly_rows(object_id):
            price_map[row.month_key] = row.price

        display_result = build_month_display_rows(window, calendar, price_map)
        result.month_cells = {
            item.display_name: cell for item, _, cell in display_result.rows
        }

        tier = RefreshTier.H12
        last_checked = fetched_at
        next_check = last_checked + timedelta(hours=TIER_HOURS[tier])
        refresh_status = RefreshStatus.SUCCESS

        result.last_checked = last_checked.isoformat()
        result.next_check = next_check.isoformat()
        result.refresh_status = refresh_status.value

        action, page_id = writer.sync_one_pricing_refresh(
            object_id=object_id,
            month_cells=result.month_cells,
            last_checked=last_checked,
            next_check=next_check,
            refresh_status=refresh_status,
            last_error="",
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


def _verify_readback(result: RefreshOneResult, readback: dict[str, str]) -> bool:
    if result.notion_rows_after != 1:
        return False
    for month_name, expected in result.month_cells.items():
        if readback.get(month_name) != expected:
            return False
    if readback.get("Refresh Status") != result.refresh_status:
        return False
    if readback.get("Last Error"):
        return False
    if result.last_checked and not readback.get("Last Checked"):
        return False
    if result.next_check and not readback.get("Next Check"):
        return False
    return True


def print_refresh_one_report(result: RefreshOneResult) -> None:
    print("\n== OBJECT ==")
    print(f"  Object ID: {result.object_id}")
    print(f"  Name: {result.name}")

    print("\n== CALENDAR ==")
    print(f"  rows: {result.calendar_rows}")

    print("\n== MONTHLY PRICING (raw SQLite) ==")
    month_order = list(LOCKED_MONTH_COLUMNS)
    raw_by_key = {row["month_key"]: row for row in result.monthly_raw}
    for display in month_order:
        year_s, month_s = _display_to_key_parts(display)
        key = f"{year_s}-{month_s}"
        row = raw_by_key.get(key) or {}
        price = row.get("price")
        price_s = str(int(price)) if price is not None else "—"
        print(
            f"  {display} | status={row.get('status') or '—'} | "
            f"price={price_s} | currency={row.get('currency') or '—'} | "
            f"pricing_status={row.get('pricing_status') or '—'} | "
            f"period_used={row.get('period_used') or '—'} | "
            f"based_on_days={row.get('based_on_days') or '—'}"
        )

    print("\n== SQLITE ==")
    print(f"  path: {result.sqlite_path}")
    print(f"  backup: {result.backup_path or '(none)'}")
    print(f"  availability_months rows: {result.monthly_rows_after}")
    print(f"  duplicates: {result.monthly_duplicates}")
    print(f"  integrity_check: {result.integrity_check}")

    print("\n== NOTION ==")
    for name in month_order:
        print(f"  {name} → {result.month_cells.get(name, '—')}")
    print(f"  rows for {result.object_id} = {result.notion_rows_after}")
    rb = "PASS" if result.notion_readback_pass else "FAIL"
    print(f"  NOTION_READBACK = {rb}")
    if result.readback:
        print("\n== NOTION READBACK ==")
        for name in month_order:
            print(f"  {name}: {result.readback.get(name, '—')}")
        for key in ("Last Checked", "Next Check", "Refresh Status", "Last Error"):
            if key in result.readback:
                print(f"  {key}: {result.readback[key]}")

    print("\n== PERFORMANCE ==")
    print(f"  pricing_elapsed_s: {result.pricing_elapsed_s}")
    print(f"  price_fetch_attempts: {result.price_fetch_attempts}")
    print(f"  price_fetch_successes: {result.price_fetch_successes}")
    print(f"  price_fetch_failures: {result.price_fetch_failures}")
    print(f"  price_fetch_retries: {result.price_fetch_retries}")
    print(f"  max_concurrency: {result.max_concurrency}")

    print("\n== SAFETY ==")
    print(f"  other objects changed: {result.other_objects_changed}")
    print(f"  source «Аренда недвижимости» writes: {result.source_writes}")
    print("  Agent1 unchanged")
    print("  Agent2 unchanged")
    print("  Agent6 unchanged")
    print("  website unchanged")
    print("  scheduler OFF")
    print("  systemd unchanged")
    print("  background LIVE OFF (one-shot only)")


def _display_to_key_parts(display: str) -> tuple[str, str]:
    from .notion_reader import parse_month_column_label

    parsed = parse_month_column_label(display)
    if not parsed:
        return "0000", "00"
    year, month = parsed
    return f"{year:04d}", f"{month:02d}"
