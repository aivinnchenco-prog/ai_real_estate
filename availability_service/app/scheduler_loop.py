"""Production rolling scheduler loop: Airbnb 48H + Facebook 78H."""
from __future__ import annotations

import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .batch_safe import _process_one_object
from .config import AvailabilityConfig, load_config
from .facebook_refresh import FacebookRefreshResult, run_facebook_refresh
from .models import AvailabilityObjectState, RefreshStatus, RefreshTier, SourceKind
from .notion_reader import NotionReader, compare_month_schema, map_source_fields, map_target_fields
from .production_scheduler import ingest_source_catalog
from .notion_writer import NotionWriter
from .repository import AvailabilityRepository
from .scheduler import AvailabilityScheduler
from .server_concurrency import (
    BatchRunMetrics,
    configure_server_concurrency,
    get_peak_browser_instances,
    reset_peak_browser_instances,
    ServerConcurrencyLimits,
)
from .models import get_availability_window
from .airbnb_workers.config import load_worker_pool_config
from .airbnb_workers.integration import build_worker_pool, worker_pool_enabled
from .airbnb_workers.registry import AirbnbWorkerRepository
from .airbnb_workers.worker_scheduler import pick_airbnb_worker_jobs


POLL_IDLE_SECONDS = 60
INGEST_INTERVAL_SECONDS = 900


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _source_live(config: AvailabilityConfig, source: SourceKind) -> bool:
    if source == SourceKind.AIRBNB:
        return config.airbnb_live_allowed
    if source == SourceKind.FACEBOOK:
        return config.facebook_live_allowed
    return False


def _fair_due_sort_key(
    item: AvailabilityObjectState,
    *,
    now: datetime,
) -> tuple:
    """Process established 48H/78H due jobs before FIRST_REFRESH backlog when both are due."""
    return (
        item.refresh_tier == RefreshTier.FIRST_REFRESH,
        item.next_check_at or now,
        item.object_id,
    )


def pick_next_due_object(
    objects: list[AvailabilityObjectState],
    config: AvailabilityConfig,
    *,
    now: datetime,
) -> AvailabilityObjectState | None:
    due: list[AvailabilityObjectState] = []
    for item in objects:
        if item.next_check_at is None or item.next_check_at > now:
            continue
        if _source_live(config, item.source):
            due.append(item)
    if not due:
        return None
    return sorted(due, key=lambda o: _fair_due_sort_key(o, now=now))[0]


def _run_source_ingest(
    config: AvailabilityConfig,
    reader: NotionReader,
    writer: NotionWriter,
    repo: AvailabilityRepository,
    target_schema,
    target_mapping,
    *,
    now: datetime,
) -> int:
    source_schema = reader.fetch_schema(config.source_database_id)
    if source_schema.error:
        return 0
    mapping = map_source_fields(source_schema.properties)
    properties = reader.read_property_batch(
        config.source_database_id,
        mapping,
        limit=5000,
    )
    result = ingest_source_catalog(
        reader,
        repo,
        properties,
        now=now,
        writer=writer if config.writes_allowed else None,
        target_schema=target_schema if config.writes_allowed else None,
        target_mapping=target_mapping if config.writes_allowed else None,
    )
    return result.scanned


@dataclass
class SchedulerLoopStats:
    processed: int = 0
    airbnb_success: int = 0
    airbnb_error: int = 0
    orphan_removed: int = 0
    facebook_active: int = 0
    facebook_sold: int = 0
    facebook_error: int = 0
    classification_errors: int = 0
    browser_errors: int = 0
    peak_browser_instances: int = 0
    errors: list[str] = field(default_factory=list)


def _apply_airbnb_result(stats: SchedulerLoopStats, result, object_id: str) -> None:
    stats.processed += 1
    if result.orphan_removed:
        stats.orphan_removed += 1
    elif result.success:
        stats.airbnb_success += 1
    else:
        stats.airbnb_error += 1
        stats.errors.append(f"{object_id}: {result.error}")
        err_lower = (result.error or "").lower()
        if "browser" in err_lower or "proxy" in err_lower:
            stats.browser_errors += 1


def run_scheduler_loop(
    config: AvailabilityConfig | None = None,
    *,
    max_iterations: int | None = None,
) -> SchedulerLoopStats:
    """Production loop. Worker pool: parallel Airbnb checks per worker; Facebook sequential."""
    config = config or load_config()
    if not config.enabled or config.dry_run:
        raise SystemExit("REFUSED: AVAILABILITY_ENABLED=true and AVAILABILITY_DRY_RUN=false required")
    if not config.airbnb_live_allowed and not config.facebook_live_allowed:
        raise SystemExit(
            "REFUSED: enable AVAILABILITY_AIRBNB_ENABLED or AVAILABILITY_FACEBOOK_ENABLED"
        )
    if not config.notion_api_key:
        raise SystemExit("NOTION_API_KEY missing")

    reader = NotionReader(config)
    writer = NotionWriter(config)
    target_schema = reader.fetch_schema(config.target_database_id)
    if target_schema.error:
        raise SystemExit(f"target schema error: {target_schema.error}")
    target_mapping = map_target_fields(target_schema.properties)
    window = get_availability_window(config.window_start_mode)
    if config.airbnb_live_allowed:
        matched, msg = compare_month_schema(window, target_mapping.month_columns)
        if not matched:
            raise SystemExit(f"MONTH_SCHEMA_MATCH {msg}")

    repo = AvailabilityRepository(config.sqlite_path)
    scheduler = AvailabilityScheduler(repo)
    metrics = BatchRunMetrics()
    stats = SchedulerLoopStats()
    iteration = 0
    last_ingest_at = 0.0
    stop_requested = False
    worker_pool = None
    worker_pool_config = load_worker_pool_config()
    use_worker_pool = worker_pool_enabled(config) and worker_pool_config.enabled
    if use_worker_pool and config.airbnb_live_allowed:
        worker_pool, worker_pool_config = build_worker_pool(repo)
        worker_pool.assign_new_objects()
        worker_pool.maybe_rebalance()

    worker_count = len(worker_pool.worker_repo.list_workers()) if worker_pool else 1
    browser_max = max(config.browser_max_instances, worker_count if use_worker_pool else 1)

    limits = ServerConcurrencyLimits(
        object_concurrency=config.object_concurrency if not use_worker_pool else worker_count,
        calendar_concurrency=config.calendar_concurrency if not use_worker_pool else worker_count,
        price_concurrency=config.price_concurrency,
        browser_max_instances=browser_max,
    )
    reset_peak_browser_instances()
    configure_server_concurrency(limits)

    def _request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    def _process_airbnb_job(object_id: str):
        thread_repo = AvailabilityRepository(config.sqlite_path)
        thread_worker_pool = worker_pool
        try:
            # Pool registry uses repo._conn; never share it across ThreadPool workers.
            if use_worker_pool:
                thread_worker_pool, _ = build_worker_pool(thread_repo)
            return _process_one_object(
                object_id,
                config=config,
                reader=reader,
                writer=writer,
                repo=thread_repo,
                window=window,
                target_schema=target_schema,
                target_mapping=target_mapping,
                batch_allowed=frozenset({object_id}),
                metrics=metrics,
                scheduler=AvailabilityScheduler(thread_repo),
                worker_pool=thread_worker_pool,
            )
        finally:
            thread_repo.close()

    try:
        _run_source_ingest(
            config, reader, writer, repo, target_schema, target_mapping, now=_utcnow()
        )
        last_ingest_at = time.time()

        while not stop_requested and (max_iterations is None or iteration < max_iterations):
            iteration += 1
            now = _utcnow()
            if time.time() - last_ingest_at >= INGEST_INTERVAL_SECONDS:
                try:
                    scanned = _run_source_ingest(
                        config, reader, writer, repo, target_schema, target_mapping, now=now
                    )
                    if use_worker_pool and worker_pool and scanned:
                        worker_pool.assign_new_objects(now=now)
                except Exception as exc:
                    stats.errors.append(f"ingest: {exc}")
                last_ingest_at = time.time()

            due_all = repo.due_objects(now=now)
            handled = False

            if use_worker_pool and worker_pool and config.airbnb_live_allowed:
                worker_repo = AirbnbWorkerRepository(repo)
                jobs = pick_airbnb_worker_jobs(
                    due_all,
                    worker_pool,
                    worker_repo,
                    worker_pool_config,
                    now=now,
                )
                if jobs:
                    handled = True
                    if max_iterations is not None:
                        jobs = jobs[: max(0, max_iterations - stats.processed)]
                    for job in jobs:
                        j, _ = repo.enqueue_job(job.object_id, now=now)
                        if j.job_id:
                            repo.mark_job_checking(j.job_id, now=now)
                    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
                        futures = {
                            executor.submit(_process_airbnb_job, job.object_id): job
                            for job in jobs
                        }
                        for future in as_completed(futures):
                            job = futures[future]
                            try:
                                result = future.result()
                            except Exception as exc:
                                stats.processed += 1
                                stats.airbnb_error += 1
                                stats.errors.append(f"{job.object_id}: {exc}")
                                continue
                            _apply_airbnb_result(stats, result, job.object_id)

            if not handled:
                item = pick_next_due_object(due_all, config, now=now)
                if item is None:
                    if max_iterations is not None:
                        break
                    time.sleep(POLL_IDLE_SECONDS)
                    continue

                job, _ = repo.enqueue_job(item.object_id, now=now)
                if job.job_id:
                    repo.mark_job_checking(job.job_id, now=now)

                if item.source == SourceKind.AIRBNB:
                    result = _process_one_object(
                        item.object_id,
                        config=config,
                        reader=reader,
                        writer=writer,
                        repo=repo,
                        window=window,
                        target_schema=target_schema,
                        target_mapping=target_mapping,
                        batch_allowed=frozenset({item.object_id}),
                        metrics=metrics,
                        scheduler=scheduler,
                        worker_pool=worker_pool,
                    )
                    _apply_airbnb_result(stats, result, item.object_id)
                elif item.source == SourceKind.FACEBOOK:
                    fb_result: FacebookRefreshResult = run_facebook_refresh(
                        item.object_id,
                        config=config,
                        reader=reader,
                        writer=writer,
                        repo=repo,
                        scheduler=scheduler,
                        target_schema=target_schema,
                        target_mapping=target_mapping,
                        confirm_write=True,
                    )
                    stats.processed += 1
                    if fb_result.notion_action == "orphan_removed":
                        stats.orphan_removed += 1
                    elif fb_result.success:
                        if fb_result.business_status == "ACTIVE":
                            stats.facebook_active += 1
                        elif fb_result.business_status == "SOLD":
                            stats.facebook_sold += 1
                    else:
                        stats.facebook_error += 1
                        stats.errors.append(f"{item.object_id}: {fb_result.error}")
                        if fb_result.technical_outcome in (
                            "BROWSER_ERROR",
                            "TIMEOUT",
                            "LOGIN_REQUIRED",
                        ):
                            stats.browser_errors += 1
                        if fb_result.technical_outcome == "UNCLASSIFIED":
                            stats.classification_errors += 1

            stats.peak_browser_instances = max(
                stats.peak_browser_instances,
                get_peak_browser_instances(),
            )
            if max_iterations is not None and stats.processed >= max_iterations:
                break
    finally:
        stats.peak_browser_instances = max(
            stats.peak_browser_instances,
            get_peak_browser_instances(),
        )
        repo.close()

    return stats


def print_scheduler_loop_stats(stats: SchedulerLoopStats) -> None:
    print("\n== SCHEDULER LOOP ==")
    print(f"  processed: {stats.processed}")
    print(f"  airbnb_success: {stats.airbnb_success}")
    print(f"  airbnb_error: {stats.airbnb_error}")
    print(f"  orphan_removed: {stats.orphan_removed}")
    print(f"  facebook_active: {stats.facebook_active}")
    print(f"  facebook_sold: {stats.facebook_sold}")
    print(f"  facebook_error: {stats.facebook_error}")
    print(f"  classification_errors: {stats.classification_errors}")
    print(f"  browser_errors: {stats.browser_errors}")
    print(f"  peak_browser_instances: {stats.peak_browser_instances}")
    if stats.errors:
        print("  errors:")
        for err in stats.errors[:20]:
            print(f"    - {err}")
