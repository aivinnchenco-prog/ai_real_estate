"""One-shot live Airbnb probe for a single object (controlled manual verification)."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from .calendar_report import (
    calendar_stats,
    format_blocked_ranges_summary,
    format_daily_calendar,
)
from .config import AvailabilityConfig, load_config, load_project_env, resolve_runtime_dir
from .models import (
    AvailabilityStatus,
    CalendarDay,
    MonthWindowItem,
    PropertySource,
    RefreshStatus,
    SourceKind,
    SourceStatus,
    TIER_HOURS,
    filter_calendar_to_window,
    get_availability_window,
    get_effective_window_start,
    window_calendar_bounds,
)
from .month_display import evaluate_month_display_status
from .notion_reader import NotionReader, map_source_fields, normalize_source
from .repository import AvailabilityRepository
from .status_mapper import AirbnbMonthPolicy, build_month_availabilities, normalize_calendar
from .stay_service import AvailabilityStayService
from ..providers.airbnb import save_provider_calendar
from ..providers.airbnb_adapter import (
    collect_prices_for_window,
    create_selenium_price_fetcher,
    fetch_calendar_days_live,
)

PROBE_OBJECT_ID = "A_20260810_003"  # first controlled probe; CLI accepts any ID with --confirm-live
PROBE_RUNTIME_DIR = Path("/opt/openhome/runtime/availability")


@dataclass
class PriceFetchMetrics:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    calendar_browser_loads: int = 0
    max_concurrency: int = 1

    def wrap(self, fetch: Callable[[date, date], Optional[float]]) -> Callable[[date, date], Optional[float]]:
        def wrapped(check_in: date, check_out: date) -> Optional[float]:
            self.attempts += 1
            try:
                value = fetch(check_in, check_out)
            except Exception:
                self.failures += 1
                return None
            if value is None:
                self.failures += 1
            else:
                self.successes += 1
            return value

        return wrapped


@dataclass
class ProbeResult:
    object_id: str = ""
    name: str = ""
    source: str = ""
    source_url: str = ""
    window: list[MonthWindowItem] = field(default_factory=list)
    calendar_raw: dict[date, bool] = field(default_factory=dict)
    month_rows: list = field(default_factory=list)
    price_entries: dict[str, dict] = field(default_factory=dict)
    stay_results: list[tuple[str, object]] = field(default_factory=list)
    metrics: PriceFetchMetrics = field(default_factory=PriceFetchMetrics)
    calendar_elapsed_s: float = 0.0
    pricing_elapsed_s: float = 0.0
    total_elapsed_s: float = 0.0
    sqlite_path: str = ""
    backup_path: str = ""
    calendar_rows_written: int = 0
    monthly_rows_written: int = 0
    duplicate_dates: int = 0
    integrity_check: str = ""
    refresh_jobs_delta: int = 0
    notion_writes: int = 0
    error: str = ""


def _probe_runtime_dir() -> Path:
    try:
        PROBE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        return PROBE_RUNTIME_DIR
    except OSError:
        fallback = resolve_runtime_dir()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _apply_probe_env() -> None:
    load_project_env()
    os.environ["AVAILABILITY_ENABLED"] = "true"
    os.environ["AVAILABILITY_AIRBNB_ENABLED"] = "true"
    os.environ["AVAILABILITY_DRY_RUN"] = "false"
    runtime = _probe_runtime_dir()
    os.environ["AVAILABILITY_RUNTIME_DIR"] = str(runtime)


def _validate_probe_request(object_id: str, confirm_live: bool) -> None:
    if not object_id or not object_id.strip():
        raise SystemExit("REFUSED: --object-id is required")
    if not confirm_live:
        raise SystemExit("REFUSED: probe-airbnb requires --confirm-live")


def _backup_sqlite(path: Path) -> str:
    if not path.exists():
        return ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(f".sqlite3.backup-{stamp}")
    shutil.copy2(path, backup)
    return str(backup)


def _find_property(reader: NotionReader, object_id: str, database_id: str) -> PropertySource | None:
    schema = reader.fetch_schema(database_id)
    if schema.error:
        raise SystemExit(f"Source schema error: {schema.error}")
    mapping = map_source_fields(schema.properties)
    if mapping.object_id.status != "mapped" or not mapping.object_id.property_name:
        raise SystemExit("Object ID column not mapped in source schema")

    prop_name = mapping.object_id.property_name
    payload = {
        "filter": {
            "property": prop_name,
            "rich_text": {"equals": object_id},
        },
        "page_size": 5,
    }
    data = reader._request("POST", f"/databases/{database_id}/query", payload)
    results = data.get("results") or []
    if not results:
        return None
    page = results[0]
    props = page.get("properties") or {}
    from .notion_reader import _plain_property

    oid = _plain_property(props.get(prop_name))
    name = _plain_property(
        props.get(mapping.name.property_name) if mapping.name and mapping.name.property_name else None
    )
    source_url = ""
    if mapping.source_url and mapping.source_url.property_name:
        source_url = _plain_property(props.get(mapping.source_url.property_name))
    raw_source = ""
    if mapping.source and mapping.source.property_name:
        raw_source = _plain_property(props.get(mapping.source.property_name))
    source = normalize_source(raw_source=raw_source, source_url=source_url, object_id=oid)
    return PropertySource(
        object_id=oid.strip(),
        name=name.strip(),
        source=source,
        source_url=source_url.strip(),
        notion_page_id=page.get("id") or "",
    )


def _check_duplicates(conn: sqlite3.Connection, object_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) - COUNT(DISTINCT date) AS dup
        FROM availability_calendar_days WHERE object_id = ?
        """,
        (object_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def run_probe_airbnb(object_id: str, *, confirm_live: bool) -> ProbeResult:
    _validate_probe_request(object_id, confirm_live)
    total_start = time.perf_counter()
    result = ProbeResult(object_id=object_id)

    _apply_probe_env()
    config = load_config()
    if not config.airbnb_live_allowed:
        raise SystemExit("Live flags not active after probe env override")

    reader = NotionReader(config)
    prop = _find_property(reader, object_id, config.source_database_id)
    if prop is None:
        raise SystemExit(f"STOP: Object ID {object_id} not found in source Notion DB")
    if prop.source != SourceKind.AIRBNB:
        raise SystemExit(f"STOP: source is {prop.source.value}, not AIRBNB")
    if "airbnb" not in prop.source_url.lower():
        raise SystemExit(f"STOP: URL is not Airbnb: {prop.source_url}")

    result.name = prop.name
    result.source = prop.source.value
    result.source_url = prop.source_url

    today = date.today()
    effective_start = get_effective_window_start(today, config.window_start_mode)
    window = get_availability_window(config.window_start_mode, now=today)
    result.window = window

    metrics = PriceFetchMetrics(max_concurrency=1)
    cal_start = time.perf_counter()
    try:
        calendar_raw = fetch_calendar_days_live(prop.source_url)
    except Exception as exc:
        raise SystemExit(f"STOP: calendar fetch failed: {exc}") from exc
    metrics.calendar_browser_loads = 1
    result.calendar_elapsed_s = round(time.perf_counter() - cal_start, 2)
    result.calendar_raw = calendar_raw

    if not calendar_raw:
        raise SystemExit("STOP: Airbnb returned empty calendar (captcha/block/missing)")

    calendar_raw = filter_calendar_to_window(calendar_raw, window)
    result.calendar_raw = calendar_raw
    if not calendar_raw:
        raise SystemExit(
            "STOP: Airbnb calendar has no days inside availability window "
            f"{window[0].display_name} → {window[-1].display_name}"
        )

    price_start = time.perf_counter()
    fetch_price, cleanup, get_metrics = create_selenium_price_fetcher(prop.source_url)
    try:
        price_entries = collect_prices_for_window(
            fetch_price,
            calendar_raw,
            window,
            min_segment_days=5,
        )
    except Exception as exc:
        cleanup()
        raise SystemExit(f"STOP: pricing failed: {exc}") from exc
    finally:
        cleanup()
    result.pricing_elapsed_s = round(time.perf_counter() - price_start, 2)
    result.price_entries = price_entries
    pm = get_metrics()
    metrics.attempts = pm.fetch_calls
    metrics.successes = sum(1 for e in price_entries.values() if e.get("price"))
    metrics.failures = pm.failed_fetch_calls
    result.metrics = metrics

    calendar_days = normalize_calendar(calendar_raw)
    result.month_rows = build_month_availabilities(
        window,
        calendar_days,
        price_entries,
        AirbnbMonthPolicy.CONTIGUOUS_STAY_REQUIRED,
        min_contiguous_days=5,
    )

    sqlite_path = config.sqlite_path
    result.sqlite_path = str(sqlite_path)
    result.backup_path = _backup_sqlite(sqlite_path)

    repo = AvailabilityRepository(sqlite_path)
    try:
        jobs_before = repo.count_active_jobs()
        repo.upsert_property(prop, default_tier=config.default_refresh_tier)
        result.calendar_rows_written = save_provider_calendar(
            repo, object_id, calendar_days, replace=True
        )
        fetched_at = datetime.now(timezone.utc)
        cal_dict = {day.date: day.available for day in calendar_days}
        display_month_rows = build_month_availabilities(
            window,
            cal_dict,
            price_entries,
            AirbnbMonthPolicy.FULL_MONTH_REQUIRED,
        )
        display_statuses = {
            item.key: evaluate_month_display_status(cal_dict, item.year, item.month).value
            for item in window
        }
        result.monthly_rows_written = repo.replace_monthly_rows(
            object_id,
            display_month_rows,
            fetched_at=fetched_at,
            display_statuses=display_statuses,
        )
        repo.set_state(
            f"probe_snapshot:{object_id}",
            json.dumps(
                {
                    "price_entries": price_entries,
                    "fetched_at": fetched_at.isoformat(),
                },
                ensure_ascii=False,
            ),
            now=fetched_at,
        )
        state = repo.get_object(object_id)
        if state is not None:
            state.last_checked_at = fetched_at
            state.next_check_at = fetched_at + timedelta(hours=TIER_HOURS[state.refresh_tier])
            state.refresh_status = RefreshStatus.SUCCESS
            state.source_status = SourceStatus.ACTIVE
            state.last_error = ""
            repo.save_object(state, now=fetched_at)
        jobs_after = repo.count_active_jobs()
        result.refresh_jobs_delta = jobs_after - jobs_before

        conn = repo._conn
        result.duplicate_dates = _check_duplicates(conn, object_id)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        result.integrity_check = integrity[0] if integrity else "fail"

        stay_service = AvailabilityStayService(repo)
        stay_cases = [
            ("A", date(2026, 9, 1), 1),
            ("B", date(2026, 9, 1), 3),
            ("C", date(2026, 9, 15), 1),
            ("D", date(2026, 9, 15), 3),
            ("E", date(2026, 10, 1), 6),
        ]
        for label, check_in, months in stay_cases:
            sr = stay_service.check_object_stay(object_id, check_in, months)
            result.stay_results.append((label, sr))
    finally:
        repo.close()

    result.total_elapsed_s = round(time.perf_counter() - total_start, 2)
    result.notion_writes = 0
    return result


def print_probe_report(result: ProbeResult) -> None:
    stats = calendar_stats(result.calendar_raw)
    window_months = [(item.year, item.month) for item in result.window]

    print("\n== OBJECT ==")
    print(f"  Object ID: {result.object_id}")
    print(f"  Name: {result.name}")
    print(f"  Source: {result.source}")
    print(f"  URL: {result.source_url}")

    print("\n== WINDOW ==")
    if result.window:
        print(f"  {result.window[0].display_name} → {result.window[-1].display_name}")

    print("\n== CALENDAR SUMMARY ==")
    for key, val in stats.items():
        print(f"  {key}: {val}")

    print("\n== DAILY CALENDAR ==")
    print(format_daily_calendar(result.calendar_raw))

    print("== BLOCKED RANGES ==")
    print(format_blocked_ranges_summary(result.calendar_raw, window_months))

    print("== MONTHLY PRICES ==")
    for item in result.month_rows:
        key = f"{item.year:04d}-{item.month:02d}"
        entry = result.price_entries.get(key) or {}
        from .models import MONTH_ABBR

        display = f"{MONTH_ABBR[item.month - 1]} {item.year % 100:02d}"
        price_s = f"{int(item.price)} {item.currency}" if item.price else "—"
        print(
            f"  {display} | {item.status.value} | {price_s} | "
            f"{item.pricing_status or '—'} | {item.period_used or '—'} | "
            f"{item.based_on_days or entry.get('available_days', '—')}"
        )

    print("\n== EXACT STAY ==")
    for label, sr in result.stay_results:
        print(
            f"  [{label}] check_in={sr.check_in} check_out={sr.check_out} "
            f"stay={sr.stay_months}M status={sr.status.value} "
            f"checked_days={len(sr.checked_days)} "
            f"blocked={len(sr.unavailable_dates)} missing={len(sr.missing_dates)}"
        )

    print("\n== PERFORMANCE ==")
    print(f"  total_elapsed_s: {result.total_elapsed_s}")
    print(f"  calendar_elapsed_s: {result.calendar_elapsed_s}")
    print(f"  pricing_elapsed_s: {result.pricing_elapsed_s}")
    m = result.metrics
    print(f"  calendar_browser_loads: {m.calendar_browser_loads}")
    print(f"  price_fetch_attempts: {m.attempts}")
    print(f"  price_fetch_successes: {m.successes}")
    print(f"  price_fetch_failures: {m.failures}")
    print(f"  max_concurrency: {m.max_concurrency}")

    print("\n== SQLITE ==")
    print(f"  path: {result.sqlite_path}")
    print(f"  backup: {result.backup_path or '(none)'}")
    print(f"  calendar_rows_written: {result.calendar_rows_written}")
    print(f"  monthly_rows_written: {result.monthly_rows_written}")
    print(f"  duplicate_dates: {result.duplicate_dates}")
    print(f"  integrity_check: {result.integrity_check}")
    print(f"  refresh_jobs_delta: {result.refresh_jobs_delta}")

    print("\n== SAFETY ==")
    print("  Notion writes: 0")
    print("  Agent1 unchanged")
    print("  Agent2 unchanged")
    print("  scheduler not started")
    print("  permanent .env unchanged")
    print("  background LIVE OFF (probe one-shot only)")
