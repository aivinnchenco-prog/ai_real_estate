"""Availability Service CLI.

Usage:
  python -m availability_service.main dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from .app.config import load_config
from .app.models import (
    DryRunSummary,
    SourceKind,
    get_availability_window,
    get_effective_window_start,
)
from .app.notion_reader import (
    NotionReader,
    classify_target_properties,
    compare_month_schema,
    map_source_fields,
    map_target_fields,
    sort_month_columns,
    validate_locked_target_schema,
)
from .app.notion_writer import NotionWriteBlocked, NotionWriter
from .app.repository import AvailabilityRepository
from .app.scheduler import AvailabilityScheduler
from .providers.airbnb import provider_readiness_label


def _print_schema(schema, label: str) -> None:
    print(f"\n== {label} ==")
    if schema.error:
        print(f"  error: {schema.error}")
    print(f"  id: {schema.database_id}")
    print(f"  title: {schema.title}")
    print(f"  object: {schema.object_kind}")
    print(f"  properties: {len(schema.properties)}")
    for name, ptype in schema.property_summaries():
        print(f"    - {name}: {ptype}")


def _print_mapping(mapping) -> None:
    print("\n== Source field mapping ==")
    for match in mapping.as_list():
        if match.status == "mapped":
            print(f"  {match.logical}: {match.property_name!r} ({match.property_type})")
        elif match.status == "ambiguous":
            print(f"  {match.logical}: AMBIGUOUS {list(match.candidates)}")
        else:
            print(f"  {match.logical}: UNMAPPED")


def _print_target_mapping(mapping) -> None:
    print("\n== Target field mapping ==")
    for match in mapping.as_list():
        if match.status == "mapped":
            print(f"  {match.logical}: {match.property_name!r} ({match.property_type})")
        elif match.status == "ambiguous":
            print(f"  {match.logical}: AMBIGUOUS {list(match.candidates)}")
        else:
            print(f"  {match.logical}: UNMAPPED")
    unmapped = mapping.unmapped()
    if unmapped:
        print("\n== Unmapped target fields ==")
        for match in unmapped:
            extra = f" candidates={list(match.candidates)}" if match.candidates else ""
            print(f"  {match.logical}: {match.status}{extra}")


def run_dry_run() -> DryRunSummary:
    config = load_config()
    summary = DryRunSummary()
    summary.notes.append("LIVE OFF")
    summary.notes.append(f"enabled={config.enabled} dry_run={config.dry_run}")
    summary.airbnb_provider_readiness = provider_readiness_label(config)
    if not config.notion_api_key:
        raise SystemExit("NOTION_API_KEY / NOTION_TOKEN is missing in .env")
    if not config.source_database_id:
        raise SystemExit("AVAILABILITY_SOURCE_NOTION_DATABASE_ID or NOTION_DB_ID is missing")

    reader = NotionReader(config)
    writer = NotionWriter(config)
    try:
        writer.upsert_availability_row(None, [])  # type: ignore[arg-type]
    except NotionWriteBlocked:
        summary.notion_writes = 0
    else:
        raise SystemExit("Writer failed to block dry-run Notion write")

    source_schema = reader.fetch_schema(config.source_database_id)
    target_schema = reader.fetch_schema(config.target_database_id)
    summary.source_schema = source_schema
    summary.target_schema = target_schema
    mapping = map_source_fields(source_schema.properties)
    summary.source_mapping = mapping
    if target_schema.properties:
        target_mapping = map_target_fields(target_schema.properties)
        summary.target_mapping = target_mapping
        month_cols, tech_cols = classify_target_properties(target_schema.properties)
        summary.month_columns = month_cols
        summary.technical_columns = tech_cols
        col_ok, col_msg = validate_locked_target_schema(target_schema.properties)
        summary.column_order_lock = col_ok
        summary.column_order_message = col_msg

    today = date.today()
    summary.window_start_mode = config.window_start_mode.value
    effective_start = get_effective_window_start(today, config.window_start_mode)
    summary.effective_window_start = effective_start
    window = get_availability_window(config.window_start_mode, now=today)
    summary.window = window

    if summary.month_columns:
        matched, message = compare_month_schema(window, summary.month_columns)
        summary.month_schema_match = matched
        summary.month_schema_message = message
    else:
        summary.month_schema_match = False
        summary.month_schema_message = "FAIL: no month columns found in target schema"

    properties = []
    if source_schema.error:
        summary.notes.append(f"source schema error: {source_schema.error}")
    else:
        properties = reader.read_property_batch(
            config.source_database_id,
            mapping,
            limit=config.dry_run_batch_size,
        )
    summary.objects_read = len(properties)
    summary.airbnb = sum(1 for item in properties if item.source == SourceKind.AIRBNB)
    summary.facebook = sum(1 for item in properties if item.source == SourceKind.FACEBOOK)
    summary.unknown = sum(1 for item in properties if item.source == SourceKind.UNKNOWN)

    repo = AvailabilityRepository(config.sqlite_path)
    try:
        jobs_before = repo.count_active_jobs()
        for item in properties:
            if not item.object_id:
                continue
            repo.upsert_property(item, default_tier=config.default_refresh_tier)
        scheduler = AvailabilityScheduler(repo)
        preview = scheduler.preview()
        would_enqueue, _ = scheduler.would_enqueue_due(limit=config.max_concurrency)
        jobs_after = repo.count_active_jobs()
        summary.due = int(preview["due"])
        summary.would_enqueue = would_enqueue
        summary.refresh_jobs_created = jobs_after - jobs_before
        tiers = preview["tiers"]
        summary.tier_1h = int(tiers.get("1H", 0))
        summary.tier_12h = int(tiers.get("12H", 0))
        summary.tier_24h = int(tiers.get("24H", 0))
        summary.notes.append(f"tier_48h={tiers.get('48H', 0)}")
        summary.notes.append(f"tier_first_refresh={tiers.get('FIRST_REFRESH', 0)}")
        repo.set_state("last_dry_run_at", datetime.now(timezone.utc).isoformat())
        repo.set_state("last_dry_run_preview", json.dumps(preview, ensure_ascii=False))
        if summary.month_schema_match:
            summary.notes.append(f"would_enqueue={would_enqueue} (preview only, no jobs created)")
        else:
            summary.notes.append("MONTH_SCHEMA_MATCH FAIL — skipping enqueue preview")
    finally:
        repo.close()

    summary.sqlite_path = str(config.sqlite_path)
    summary.airbnb_requests = 0
    summary.facebook_requests = 0
    return summary


def print_summary(summary: DryRunSummary) -> None:
    if summary.source_schema:
        _print_schema(summary.source_schema, "Source DB «Аренда недвижимости»")
    if summary.source_mapping:
        _print_mapping(summary.source_mapping)
        unmapped = summary.source_mapping.unmapped()
        if unmapped:
            print("\n== Unmapped source fields ==")
            for match in unmapped:
                extra = f" candidates={list(match.candidates)}" if match.candidates else ""
                print(f"  {match.logical}: {match.status}{extra}")
    if summary.target_schema:
        _print_schema(summary.target_schema, "Target DB «Аренда недвижимости — Доступность»")
        if summary.target_schema.data_source_id:
            print(f"  data_source_id: {summary.target_schema.data_source_id}")
        if summary.target_mapping:
            _print_target_mapping(summary.target_mapping)
            print("\n== Target columns (display order: months → technical) ==")
            for name in summary.target_mapping.display_column_order():
                print(f"  - {name}")
        print("\n== Target month columns ==")
        if summary.month_columns:
            for name in summary.month_columns:
                print(f"  - {name}")
        else:
            print("  (none found)")
        print("\n== Target technical columns ==")
        if summary.technical_columns:
            for name in summary.technical_columns:
                print(f"  - {name}")
        else:
            print("  (none found)")

    print("\n== Effective availability window ==")
    print(f"  mode: {summary.window_start_mode}")
    if summary.effective_window_start:
        print(f"  effective_window_start: {summary.effective_window_start.isoformat()}")

    print("\n== Rolling 12-month window ==")
    for item in summary.window:
        print(f"  {item.key}  {item.display_name}")

    notion_months = sort_month_columns(summary.month_columns)
    print("\n== Notion month columns (sorted) ==")
    if notion_months:
        for name in notion_months:
            print(f"  - {name}")
    else:
        print("  (none)")

    print(f"\n== MONTH_SCHEMA_MATCH ==")
    print(f"  {summary.month_schema_message}")

    print(f"\n== COLUMN_ORDER_LOCK ==")
    print(f"  {summary.column_order_message}")

    print("\n== SQLite ==")
    print(f"  path: {summary.sqlite_path}")

    print("\n== Dry-run summary ==")
    print(f"  objects_read: {summary.objects_read}")
    print(f"  AIRBNB: {summary.airbnb}")
    print(f"  FACEBOOK: {summary.facebook}")
    print(f"  UNKNOWN: {summary.unknown}")
    print(f"  due: {summary.due}")
    print(f"  would_enqueue: {summary.would_enqueue}")
    print(f"  refresh_jobs created by dry-run: {summary.refresh_jobs_created}")
    print(f"  1H: {summary.tier_1h}")
    print(f"  12H: {summary.tier_12h}")
    print(f"  24H: {summary.tier_24h}")
    print(f"  Notion writes: {summary.notion_writes}")
    print(f"  Airbnb requests: {summary.airbnb_requests}")
    print(f"  Facebook requests: {summary.facebook_requests}")
    print(f"  Airbnb provider: {summary.airbnb_provider_readiness}")
    print(f"  Daily calendar storage: {summary.daily_calendar_storage}")
    print(f"  Exact stay matcher: {summary.exact_stay_matcher}")
    print(f"  Date ranges: {summary.date_ranges}")
    print(f"  Freshness: {summary.freshness}")
    print(f"  Agent6 interface: {summary.agent6_interface}")
    print(f"  Website calendar interface: {summary.website_calendar_interface}")
    for note in summary.notes:
        print(f"  note: {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="availability_service")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("dry-run", help="Network-safe schema and scheduler preview")

    probe = sub.add_parser("probe-airbnb", help="One-shot live Airbnb probe (single object)")
    probe.add_argument("--object-id", required=True, help="Объект ID из Notion (например A_20260810_003)")
    probe.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required to allow live Airbnb browser requests",
    )

    sync_one = sub.add_parser("sync-one", help="One-shot Notion write for a single object")
    sync_one.add_argument("--object-id", required=True, help="Объект ID (например A_20260810_003)")
    sync_one.add_argument(
        "--confirm-write",
        action="store_true",
        help="Required to allow Notion write to target «Доступность»",
    )

    refresh_one = sub.add_parser(
        "refresh-one-airbnb",
        help="One-shot monthly pricing refresh + Notion update (single object)",
    )
    refresh_one.add_argument("--object-id", required=True, help="Объект ID (например A_20260807_001)")
    refresh_one.add_argument("--pricing", action="store_true", help="Fetch live monthly pricing")
    refresh_one.add_argument("--sync-notion", action="store_true", help="Update existing Notion row")
    refresh_one.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required to allow live Airbnb pricing requests",
    )
    refresh_one.add_argument(
        "--confirm-write",
        action="store_true",
        help="Required to allow Notion write to target «Доступность»",
    )

    sync_month = sub.add_parser(
        "sync-month-display",
        help="Update month column display from SQLite (no live fetch)",
    )
    sync_month.add_argument("--object-id", required=True, help="Объект ID (например A_20260807_001)")
    sync_month.add_argument(
        "--confirm-write",
        action="store_true",
        help="Required to allow Notion month-column write",
    )

    batch_safe = sub.add_parser(
        "batch-safe",
        help="VPS-safe sequential batch (calendar → pricing → SQLite → Notion)",
    )
    batch_safe.add_argument(
        "--object-id",
        required=True,
        help="Comma-separated Object IDs (max AVAILABILITY_BATCH_SAFE_MAX_OBJECTS)",
    )
    batch_safe.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required to allow live Airbnb requests",
    )
    batch_safe.add_argument(
        "--confirm-write",
        action="store_true",
        help="Required to allow Notion writes",
    )

    scheduler_sim = sub.add_parser(
        "scheduler-simulate",
        help="Dry-run 48H rolling scheduler simulation (no live refresh)",
    )
    scheduler_sim.add_argument(
        "--object-count",
        type=int,
        default=1000,
        help="Number of objects to simulate (default 1000)",
    )

    scheduler_bootstrap = sub.add_parser(
        "scheduler-bootstrap",
        help="Production bootstrap: ingest source, spread schedule, preview (no live refresh)",
    )

    scheduler_loop = sub.add_parser(
        "scheduler-loop",
        help="Production rolling scheduler (Airbnb 48H + Facebook 78H)",
    )
    scheduler_loop.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Stop after N objects (smoke test); default runs forever",
    )

    production_go_live = sub.add_parser(
        "production-go-live",
        help="Live bootstrap: ingest, migrate 12H, Notion upsert, spread",
    )

    fb_verify = sub.add_parser(
        "facebook-verify",
        help="Controlled ACTIVE/SOLD verification with reference URLs",
    )
    fb_verify.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required to allow live Facebook browser requests",
    )

    remove_obj = sub.add_parser(
        "remove-object",
        help="Remove object from availability SQLite (orphan cleanup)",
    )
    remove_obj.add_argument("--object-id", required=True, help="Object ID to remove")
    remove_obj.add_argument(
        "--confirm",
        action="store_true",
        help="Required to delete rows (use --dry-run to preview)",
    )
    remove_obj.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview row counts without deleting",
    )

    workers = sub.add_parser("airbnb-workers", help="Airbnb calendar worker pool")
    workers_sub = workers.add_subparsers(dest="workers_command", required=True)
    workers_sub.add_parser("status", help="Worker pool diagnostics")
    workers_sub.add_parser("bootstrap", help="Bootstrap workers from proxy pool")
    migrate = workers_sub.add_parser("migrate", help="Assign Airbnb objects to workers")
    migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview balanced assignment without writing",
    )
    migrate.add_argument(
        "--confirm",
        action="store_true",
        help="Required to write assignments",
    )
    workers_sub.add_parser("test-proxies", help="Per-worker proxy connectivity (no Airbnb)")
    trace = workers_sub.add_parser("calendar-trace", help="Diagnostic calendar trace (no Notion write)")
    trace.add_argument("--object-id", required=True)
    trace.add_argument("--worker-id", required=True)
    trace.add_argument("--url", required=True)
    smoke = workers_sub.add_parser("calendar-smoke", help="Controlled calendar smoke (assigned worker)")
    smoke.add_argument("--case", action="append", default=[], help="object_id|url")
    pdry = workers_sub.add_parser("pricing-dry-run", help="Show calendar/pricing worker identity mapping")
    pdry.add_argument("--object-id", action="append", required=True)
    tpp = workers_sub.add_parser("test-pricing-proxies", help="Selenium proxy connectivity per worker")
    tpp.add_argument("--worker-id", action="append", default=[])

    args = parser.parse_args(argv)
    if args.command == "dry-run":
        summary = run_dry_run()
        print_summary(summary)
        return 0
    if args.command == "probe-airbnb":
        from .app.probe_airbnb import print_probe_report, run_probe_airbnb

        result = run_probe_airbnb(args.object_id, confirm_live=args.confirm_live)
        print_probe_report(result)
        return 0
    if args.command == "sync-one":
        from .app.sync_one import print_sync_one_report, run_sync_one

        result = run_sync_one(args.object_id, confirm_write=args.confirm_write)
        print_sync_one_report(result)
        return 0
    if args.command == "refresh-one-airbnb":
        from .app.refresh_one_airbnb import print_refresh_one_report, run_refresh_one_airbnb

        result = run_refresh_one_airbnb(
            args.object_id,
            pricing=args.pricing,
            sync_notion=args.sync_notion,
            confirm_live=args.confirm_live,
            confirm_write=args.confirm_write,
        )
        print_refresh_one_report(result)
        return 0
    if args.command == "sync-month-display":
        from .app.sync_month_display import print_sync_month_display_report, run_sync_month_display

        result = run_sync_month_display(args.object_id, confirm_write=args.confirm_write)
        print_sync_month_display_report(result)
        return 0
    if args.command == "batch-safe":
        from .app.batch_safe import print_batch_safe_report, run_batch_safe

        ids = [part.strip() for part in args.object_id.split(",") if part.strip()]
        result = run_batch_safe(
            ids,
            confirm_live=args.confirm_live,
            confirm_write=args.confirm_write,
        )
        print_batch_safe_report(result)
        return 0
    if args.command == "scheduler-simulate":
        from .app.production_scheduler import (
            print_scheduler_simulation_report,
            run_scheduler_dry_run,
            run_scheduler_simulation,
        )

        sim = run_scheduler_simulation(args.object_count)
        report = run_scheduler_dry_run()
        report["simulation"]["object_count"] = sim.object_count
        report["simulation"]["spacing_seconds"] = round(sim.spacing_seconds, 1)
        report["simulation"]["spacing_minutes"] = round(sim.spacing_seconds / 60, 2)
        report["simulation"]["objects_per_hour_avg"] = round(sim.objects_per_hour_avg, 2)
        report["simulation"]["largest_hourly_bucket"] = sim.largest_hourly_bucket
        report["simulation"]["full_sweep_hours"] = round(sim.full_sweep_hours, 2)
        report["simulation"]["min_interval_minutes"] = round(sim.min_interval_seconds / 60, 2)
        report["simulation"]["max_interval_minutes"] = round(sim.max_interval_seconds / 60, 2)
        report["simulation"]["avg_interval_minutes"] = round(sim.avg_interval_seconds / 60, 2)
        print_scheduler_simulation_report(report)
        return 0
    if args.command == "scheduler-bootstrap":
        from .app.production_scheduler import (
            print_scheduler_bootstrap_report,
            run_scheduler_bootstrap,
        )

        report = run_scheduler_bootstrap()
        print_scheduler_bootstrap_report(report)
        return 0
    if args.command == "scheduler-loop":
        from .app.scheduler_loop import print_scheduler_loop_stats, run_scheduler_loop

        stats = run_scheduler_loop(max_iterations=args.max_iterations)
        print_scheduler_loop_stats(stats)
        return 0
    if args.command == "production-go-live":
        from .app.production_scheduler import (
            print_production_go_live_report,
            run_production_go_live,
        )

        report = run_production_go_live()
        print_production_go_live_report(report)
        return 0
    if args.command == "facebook-verify":
        from .app.facebook_verify import print_facebook_verify_report, run_facebook_verify

        report = run_facebook_verify(confirm_live=args.confirm_live)
        print_facebook_verify_report(report)
        return 0 if report.verified else 1
    if args.command == "remove-object":
        from .app.remove_object import print_remove_object_report, run_remove_object

        result = run_remove_object(
            args.object_id,
            confirm=args.confirm,
            dry_run=args.dry_run,
        )
        print_remove_object_report(result)
        return 0 if result.existed or result.dry_run else 1
    if args.command == "airbnb-workers":
        from .app.airbnb_workers.cli import (
            run_airbnb_workers_bootstrap,
            run_airbnb_workers_migrate,
            run_airbnb_workers_status,
        )

        config = load_config()
        repo = AvailabilityRepository(config.sqlite_path)
        try:
            if args.workers_command == "status":
                run_airbnb_workers_status(repo)
                return 0
            if args.workers_command == "bootstrap":
                run_airbnb_workers_bootstrap(repo)
                return 0
            if args.workers_command == "migrate":
                if args.dry_run:
                    run_airbnb_workers_migrate(repo, dry_run=True)
                    return 0
                if not args.confirm:
                    raise SystemExit("REFUSED: migrate requires --confirm (use --dry-run to preview)")
                run_airbnb_workers_migrate(repo, dry_run=False)
                return 0
            if args.workers_command == "test-proxies":
                from .app.airbnb_workers.proxy_probe import (
                    print_proxy_probe_report,
                    run_proxy_probe,
                )

                report = run_proxy_probe(repo)
                print_proxy_probe_report(report)
                return 0 if report.passed_count == len(report.results) else 1
            if args.workers_command == "calendar-trace":
                from .app.airbnb_workers.calendar_diag import (
                    print_trace_matrix,
                    run_trace_matrix,
                )
                from .app.airbnb_workers.calendar_fetch import normalize_listing_url

                rows = run_trace_matrix(
                    repo,
                    cases=[(args.object_id, args.worker_id, normalize_listing_url(args.url))],
                )
                print_trace_matrix(rows)
                return 0 if rows[0].status == "SUCCESS" else 1
            if args.workers_command == "calendar-smoke":
                from .app.airbnb_workers.calendar_diag import (
                    print_trace_matrix,
                    run_controlled_smoke,
                )

                cases = []
                for item in args.case:
                    oid, url = item.split("|", 1)
                    cases.append((oid.strip(), url.strip()))
                rows = run_controlled_smoke(repo, cases)
                print_trace_matrix(rows)
                passed = sum(1 for r in rows if r.status == "SUCCESS")
                print(f"\nSMOKE: {passed}/{len(rows)} PASS")
                return 0 if passed == len(rows) else 1
            if args.workers_command == "pricing-dry-run":
                from .app.airbnb_workers.pricing_diag import (
                    build_pricing_dry_run,
                    print_pricing_dry_run,
                )

                report = build_pricing_dry_run(repo, args.object_id)
                print_pricing_dry_run(report)
                return 0
            if args.workers_command == "test-pricing-proxies":
                from .app.airbnb_workers.config import load_worker_pool_config
                from .app.airbnb_workers.pool import AirbnbWorkerPool
                from .app.airbnb_workers.pricing_diag import probe_pricing_worker_connectivity
                from .app.airbnb_workers.registry import AirbnbWorkerRepository

                pool_config = load_worker_pool_config()
                pool = AirbnbWorkerPool(AirbnbWorkerRepository(repo), pool_config)
                pool.bootstrap()
                worker_ids = args.worker_id or [
                    w.worker_id for w in pool.worker_repo.list_workers()[:3]
                ]
                results = [
                    probe_pricing_worker_connectivity(pool, wid) for wid in worker_ids
                ]
                passed = sum(1 for r in results if r.passed)
                for r in results:
                    status = "PASS" if r.passed else "FAIL"
                    print(
                        f"  {r.worker_id} [{status}] exit_ip={r.exit_ip or '-'} "
                        f"ua_ok={r.ua_ok} profile={r.profile_exists}"
                    )
                print(f"\nPRICING CONNECTIVITY: {passed}/{len(results)} PASS")
                return 0 if passed == len(results) else 1
        finally:
            repo.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
