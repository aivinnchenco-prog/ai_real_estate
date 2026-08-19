"""VPS-safe sequential batch: calendar → pricing → SQLite → Notion per object."""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .config import AvailabilityConfig, load_config, load_project_env
from .models import (
    RefreshStatus,
    RefreshTier,
    SourceKind,
    SourceStatus,
    TIER_HOURS,
    filter_calendar_to_window,
    get_availability_window,
)
from .month_display import build_month_display_rows, evaluate_month_display_status
from .notion_reader import NotionReader, compare_month_schema, map_target_fields
from .notion_writer import NotionWriter
from .probe_airbnb import _backup_sqlite
from .repository import AvailabilityRepository
from .scheduler import AvailabilityScheduler
from .scheduler_policy import production_tier_for_source
from .server_concurrency import (
    BatchRunMetrics,
    clear_batch_context,
    configure_server_concurrency,
    get_peak_browser_instances,
    price_fetch_slot,
    object_processing_slot,
    recommend_price_concurrency,
    reset_peak_browser_instances,
    set_batch_context,
    ServerConcurrencyLimits,
)
from .status_mapper import AirbnbMonthPolicy, build_month_availabilities, normalize_calendar
from .sync_one import _calendar_range_for_window, _find_source_property
from ..providers.airbnb import save_provider_calendar
from ..providers.airbnb_adapter import (
    collect_prices_for_window,
    create_selenium_price_fetcher,
    fetch_calendar_days_live,
)


def _apply_batch_live_env() -> None:
    load_project_env()
    os.environ["AVAILABILITY_ENABLED"] = "true"
    os.environ["AVAILABILITY_AIRBNB_ENABLED"] = "true"
    os.environ["AVAILABILITY_DRY_RUN"] = "false"


@dataclass
class BatchSafeObjectResult:
    object_id: str = ""
    name: str = ""
    success: bool = False
    calendar_rows: int = 0
    monthly_rows: int = 0
    notion_updated: bool = False
    notion_action: str = ""
    elapsed_s: float = 0.0
    calendar_elapsed_s: float = 0.0
    pricing_elapsed_s: float = 0.0
    price_attempts: int = 0
    price_months_requested: int = 0
    price_attempts_total: int = 0
    price_internal_retries: int = 0
    price_failed_attempts: int = 0
    price_successful_months: int = 0
    price_no_price_results: int = 0
    price_timeout_results: int = 0
    price_access_denied_results: int = 0
    price_browser_error_results: int = 0
    price_object_wallclock_capped: bool = False
    retries: int = 0
    month_cells: dict[str, str] = field(default_factory=dict)
    blocked_ranges: dict[str, str] = field(default_factory=dict)
    error: str = ""


@dataclass
class BatchSafeResult:
    object_ids: list[str] = field(default_factory=list)
    metrics: BatchRunMetrics = field(default_factory=BatchRunMetrics)
    objects: list[BatchSafeObjectResult] = field(default_factory=list)
    price_concurrency_recommendation: str = ""
    sqlite_path: str = ""
    backup_path: str = ""


def _validate_batch_request(
    object_ids: list[str],
    *,
    confirm_live: bool,
    confirm_write: bool,
    config: AvailabilityConfig,
) -> list[str]:
    if not confirm_live:
        raise SystemExit("REFUSED: batch-safe requires --confirm-live")
    if not confirm_write:
        raise SystemExit("REFUSED: batch-safe requires --confirm-write")
    ids = [oid.strip() for oid in object_ids if oid and oid.strip()]
    if not ids:
        raise SystemExit("REFUSED: --object-id required (comma-separated, max batch size)")
    if len(ids) > config.batch_safe_max_objects:
        raise SystemExit(
            f"REFUSED: batch-safe max {config.batch_safe_max_objects} objects, got {len(ids)}"
        )
    if config.object_concurrency != 1:
        raise SystemExit(
            f"REFUSED: SAFE SERVER MODE requires AVAILABILITY_OBJECT_CONCURRENCY=1 "
            f"(got {config.object_concurrency})"
        )
    if config.calendar_concurrency != 1:
        raise SystemExit(
            f"REFUSED: SAFE SERVER MODE requires AVAILABILITY_CALENDAR_CONCURRENCY=1 "
            f"(got {config.calendar_concurrency})"
        )
    return ids


def _process_one_object(
    object_id: str,
    *,
    config: AvailabilityConfig,
    reader: NotionReader,
    writer: NotionWriter,
    repo: AvailabilityRepository,
    window: list,
    target_schema,
    target_mapping,
    batch_allowed: frozenset[str],
    metrics: BatchRunMetrics,
    scheduler: AvailabilityScheduler | None = None,
) -> BatchSafeObjectResult:
    out = BatchSafeObjectResult(object_id=object_id)
    set_batch_context(object_id, metrics)
    obj_start = time.perf_counter()
    try:
        prop = _find_source_property(reader, config, object_id)
        out.name = prop.name
        if prop.source != SourceKind.AIRBNB:
            raise RuntimeError(f"source is {prop.source.value}, expected AIRBNB")

        pricing_url = prop.calendar_url.strip() or prop.source_url.strip()
        if not pricing_url or "airbnb" not in pricing_url.lower():
            raise RuntimeError(f"no Airbnb URL for pricing: {pricing_url!r}")

        with object_processing_slot(object_id, metrics):
            cal_start = time.perf_counter()
            try:
                calendar_raw = fetch_calendar_days_live(pricing_url)
            except Exception as exc:
                metrics.calendar_failures += 1
                metrics.calendar_failure_objects.append(object_id)
                metrics.record_error(f"{object_id} calendar: {exc}")
                raise

            out.calendar_elapsed_s = round(time.perf_counter() - cal_start, 2)

            if not calendar_raw:
                metrics.calendar_failures += 1
                metrics.calendar_failure_objects.append(object_id)
                raise RuntimeError("Airbnb returned empty calendar")

            calendar_raw = filter_calendar_to_window(calendar_raw, window)
            if not calendar_raw:
                raise RuntimeError("calendar empty inside availability window")

            calendar_days = normalize_calendar(calendar_raw)

            fetched_at = datetime.now(timezone.utc)
            repo.upsert_property(prop, default_tier=config.default_refresh_tier, now=fetched_at)

            out.calendar_rows = save_provider_calendar(
                repo, object_id, calendar_days, replace=True, fetched_at=fetched_at
            )

            cal_dict = {day.date: day.available for day in calendar_days}

            price_start = time.perf_counter()
            with price_fetch_slot(object_id, metrics):
                fetch_price, cleanup, get_metrics = create_selenium_price_fetcher(pricing_url)
                price_metrics = get_metrics()
                price_metrics.months_requested = len(window)
                try:
                    price_entries = collect_prices_for_window(
                        fetch_price,
                        cal_dict,
                        window,
                        min_segment_days=5,
                        metrics=price_metrics,
                        price_object_max_seconds=config.price_object_max_seconds,
                    )
                    out.price_months_requested = price_metrics.months_requested
                    out.price_attempts_total = price_metrics.attempts_total
                    out.price_internal_retries = price_metrics.internal_retries
                    out.price_failed_attempts = price_metrics.failed_fetch_calls
                    out.price_successful_months = price_metrics.successful_months
                    out.price_no_price_results = price_metrics.price_no_price_results
                    out.price_timeout_results = price_metrics.price_timeout_results
                    out.price_access_denied_results = price_metrics.price_access_denied_results
                    out.price_browser_error_results = price_metrics.price_browser_error_results
                    out.price_object_wallclock_capped = price_metrics.price_object_wallclock_capped
                    out.price_attempts = price_metrics.attempts_total
                    out.retries = price_metrics.internal_retries
                except Exception as exc:
                    metrics.pricing_failures += 1
                    metrics.record_error(f"{object_id} pricing: {exc}")
                    raise
                finally:
                    cleanup()
            out.pricing_elapsed_s = round(time.perf_counter() - price_start, 2)

            display_statuses = {
                item.key: evaluate_month_display_status(cal_dict, item.year, item.month).value
                for item in window
            }
            month_rows = build_month_availabilities(
                window,
                cal_dict,
                price_entries,
                AirbnbMonthPolicy.FULL_MONTH_REQUIRED,
            )
            out.monthly_rows = repo.replace_monthly_rows(
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
            state = repo.get_object(object_id)
            if state is not None and scheduler is None:
                state.last_checked_at = fetched_at
                state.next_check_at = fetched_at + timedelta(hours=TIER_HOURS[state.refresh_tier])
                state.refresh_status = RefreshStatus.SUCCESS
                state.source_status = SourceStatus.ACTIVE
                state.last_error = ""
                repo.save_object(state, now=fetched_at)
            elif state is not None and scheduler is not None:
                state.last_calendar_refresh_at = fetched_at
                state.source_status = SourceStatus.ACTIVE
                repo.save_object(state, now=fetched_at)

            price_map = {row.month_key: row.price for row in repo.get_monthly_rows(object_id)}
            display_result = build_month_display_rows(window, cal_dict, price_map)
            out.month_cells = {
                item.display_name: cell for item, _, cell in display_result.rows
            }
            for item in window:
                if item.key in display_result.blocked_ranges_by_key:
                    out.blocked_ranges[item.display_name] = (
                        display_result.blocked_ranges_by_key[item.key]
                    )

            if scheduler is not None:
                next_check = scheduler.schedule_success(object_id, now=fetched_at)
                notion_tier = repo.get_object(object_id).refresh_tier if repo.get_object(object_id) else production_tier_for_source(SourceKind.AIRBNB)
            else:
                notion_tier = RefreshTier.H12
                next_check = fetched_at + timedelta(hours=TIER_HOURS[notion_tier])
            action, page_id = writer.sync_batch_notion_upsert(
                object_id=object_id,
                object_name=prop.name,
                source=prop.source,
                calendar_url=prop.calendar_url.strip() or prop.source_url.strip(),
                batch_allowed_ids=batch_allowed,
                month_cells=out.month_cells,
                last_checked=fetched_at,
                next_check=next_check,
                refresh_status=RefreshStatus.SUCCESS,
                last_error="",
                target_schema=target_schema,
                target_mapping=target_mapping,
                refresh_tier=notion_tier,
            )
            after = writer.count_target_rows_by_object_id(
                config.target_database_id,
                target_mapping,
                object_id,
                target_schema.properties,
            )
            if after != 1:
                raise RuntimeError(
                    f"expected 1 Notion row after upsert for {object_id}, found {after}"
                )
            out.notion_action = action
            out.notion_updated = True
            out.success = True
    except Exception as exc:
        out.error = str(exc)
        metrics.record_error(f"{object_id}: {exc}")
        if scheduler is not None and object_id:
            try:
                scheduler.schedule_retry(object_id, str(exc)[:500])
            except KeyError:
                pass
    finally:
        out.elapsed_s = round(time.perf_counter() - obj_start, 2)
        clear_batch_context()

    return out


def run_batch_safe(
    object_ids: list[str],
    *,
    confirm_live: bool = False,
    confirm_write: bool = False,
) -> BatchSafeResult:
    _apply_batch_live_env()
    config = load_config()
    ids = _validate_batch_request(object_ids, confirm_live=confirm_live, confirm_write=confirm_write, config=config)

    if not config.airbnb_live_allowed:
        raise SystemExit("STOP: live Airbnb flags not active")
    if not config.notion_api_key:
        raise SystemExit("NOTION_API_KEY missing")

    limits = ServerConcurrencyLimits(
        object_concurrency=config.object_concurrency,
        calendar_concurrency=config.calendar_concurrency,
        price_concurrency=config.price_concurrency,
        browser_max_instances=config.browser_max_instances,
    )
    reset_peak_browser_instances()
    configure_server_concurrency(limits)

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

    batch_allowed = frozenset(ids)
    result = BatchSafeResult(object_ids=ids)
    result.metrics.object_ids = list(ids)
    result.sqlite_path = str(config.sqlite_path)
    result.backup_path = _backup_sqlite(config.sqlite_path)

    repo = AvailabilityRepository(config.sqlite_path)
    try:
        for oid in ids:
            obj_result = _process_one_object(
                oid,
                config=config,
                reader=reader,
                writer=writer,
                repo=repo,
                window=window,
                target_schema=target_schema,
                target_mapping=target_mapping,
                batch_allowed=batch_allowed,
                metrics=result.metrics,
            )
            result.objects.append(obj_result)
    finally:
        repo.close()

    result.metrics.peak_browser_instances = max(
        result.metrics.peak_browser_instances,
        get_peak_browser_instances(),
    )
    result.price_concurrency_recommendation = recommend_price_concurrency(result.metrics)
    return result


def print_batch_safe_report(result: BatchSafeResult) -> None:
    print("\n== BATCH SAFE SERVER MODE ==")
    print(f"  objects: {', '.join(result.object_ids)}")
    print(f"  sqlite: {result.sqlite_path}")
    print(f"  backup: {result.backup_path or '(none)'}")

    print("\n== PER OBJECT ==")
    for obj in result.objects:
        status = "OK" if obj.success else "FAIL"
        print(f"  [{status}] {obj.object_id} — {obj.name}")
        if obj.error:
            print(f"    error: {obj.error}")
        else:
            print(f"    calendar_rows: {obj.calendar_rows}")
            print(f"    monthly_rows: {obj.monthly_rows}")
            print(f"    notion_updated: {obj.notion_updated}")
            if obj.notion_action:
                print(f"    notion_action: {obj.notion_action}")
            print(f"    elapsed_s: {obj.elapsed_s}")
            print(f"    calendar_elapsed_s: {obj.calendar_elapsed_s}")
            print(f"    pricing_elapsed_s: {obj.pricing_elapsed_s}")
            print(f"    price_months_requested: {obj.price_months_requested}")
            print(f"    price_attempts_total: {obj.price_attempts_total}")
            print(f"    price_internal_retries: {obj.price_internal_retries}")
            print(f"    price_failed_attempts: {obj.price_failed_attempts}")
            print(f"    price_successful_months: {obj.price_successful_months}")
            print(f"    price_no_price_results: {obj.price_no_price_results}")
            print(f"    price_timeout_results: {obj.price_timeout_results}")
            print(f"    price_access_denied_results: {obj.price_access_denied_results}")
            print(f"    price_browser_error_results: {obj.price_browser_error_results}")
            print(f"    price_object_wallclock_capped: {obj.price_object_wallclock_capped}")
            print(f"    price_attempts: {obj.price_attempts}")
            print(f"    retries: {obj.retries}")
            elapsed = result.metrics.per_object_elapsed_s.get(obj.object_id)
            if elapsed is not None:
                print(f"    object_slot_elapsed_s: {elapsed}")

    print("\n== MONTH DISPLAY (success objects) ==")
    for obj in result.objects:
        if not obj.success:
            continue
        print(f"  --- {obj.object_id} ---")
        for name, cell in obj.month_cells.items():
            print(f"    {name} → {cell}")

    if any(obj.blocked_ranges for obj in result.objects):
        print("\n== BLOCKED RANGES (PARTIAL) ==")
        for obj in result.objects:
            if not obj.blocked_ranges:
                continue
            for name, ranges in obj.blocked_ranges.items():
                print(f"  {obj.object_id} {name}: {ranges}")

    m = result.metrics
    print("\n== METRICS ==")
    print(f"  peak_browser_instances: {m.peak_browser_instances}")
    print(f"  peak_ram_mb: {m.peak_ram_mb}")
    print(f"  calendar_failures: {m.calendar_failures}")
    print(f"  access_denied_count: {m.access_denied_count}")
    print(f"  browser_crash_count: {m.browser_crash_count}")
    print(f"  pricing_failures: {m.pricing_failures}")
    print(f"  per_object_elapsed_s: {m.per_object_elapsed_s}")

    print("\n== PRICE CONCURRENCY RECOMMENDATION ==")
    print(f"  {result.price_concurrency_recommendation}")
    print("  calendar_concurrency: KEEP at 1 until separate approval")

    if m.errors:
        print("\n== ERRORS ==")
        for err in m.errors:
            print(f"  {err}")

    print("\n== SAFETY ==")
    print("  sequential object processing enforced")
    print("  calendar single-flight enforced")
    print("  Agent1 unchanged")
    print("  scheduler OFF")
