"""Production rolling scheduler: source ingest, bootstrap spread, simulation (no live loop)."""
from __future__ import annotations

import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import AvailabilityConfig, load_config
from .models import (
    AvailabilityObjectState,
    PropertySource,
    RefreshStatus,
    RefreshTier,
    SourceKind,
)
from .notion_reader import NotionReader, map_source_fields
from .repository import AvailabilityRepository
from .scheduler import AvailabilityScheduler, select_due_objects
from .scheduler_policy import (
    FACEBOOK_ROLLING_WINDOW_HOURS,
    PRODUCTION_ROLLING_WINDOW_HOURS,
    spread_next_check_schedule,
    spacing_seconds_for_count,
    TARGET_OBJECTS_PER_HOUR,
)

from .notion_writer import NotionWriteBlocked

BOOTSTRAP_SPACING_MINUTES = 3.0
BURST_DUE_THRESHOLD = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SourceIngestResult:
    scanned: int = 0
    airbnb: int = 0
    facebook: int = 0
    new_objects: list[str] = field(default_factory=list)
    updated_objects: list[str] = field(default_factory=list)
    skipped_unknown: int = 0
    notion_create_planned: list[str] = field(default_factory=list)
    notion_created: list[str] = field(default_factory=list)
    notion_write_blocked: bool = False


@dataclass
class BootstrapSpreadResult:
    candidates: list[str] = field(default_factory=list)
    spread_count: int = 0
    window_hours: float = 0.0
    spacing_minutes: float = 0.0
    preserved_count: int = 0
    schedule: dict[str, datetime] = field(default_factory=dict)
    largest_hourly_bucket: int = 0


@dataclass
class DueBucketPreview:
    now: list[str] = field(default_factory=list)
    next_1h: list[str] = field(default_factory=list)
    next_6h: list[str] = field(default_factory=list)
    next_12h: list[str] = field(default_factory=list)
    next_24h: list[str] = field(default_factory=list)
    next_48h: list[str] = field(default_factory=list)
    earliest: datetime | None = None
    latest: datetime | None = None


@dataclass
class SchedulerBootstrapReport:
    source_total: int = 0
    source_airbnb: int = 0
    source_facebook: int = 0
    source_unknown: int = 0
    ingest: SourceIngestResult = field(default_factory=SourceIngestResult)
    existing_objects: int = 0
    new_objects: list[str] = field(default_factory=list)
    tier_counts: dict[str, int] = field(default_factory=dict)
    retry_objects: list[str] = field(default_factory=list)
    error_objects: list[str] = field(default_factory=list)
    overdue_24h: list[str] = field(default_factory=list)
    spread: BootstrapSpreadResult = field(default_factory=BootstrapSpreadResult)
    spread_facebook: BootstrapSpreadResult = field(default_factory=BootstrapSpreadResult)
    due_airbnb: DueBucketPreview = field(default_factory=DueBucketPreview)
    due_facebook: DueBucketPreview = field(default_factory=DueBucketPreview)
    tier_counts_facebook: dict[str, int] = field(default_factory=dict)
    sqlite_backup_path: str = ""
    sqlite_integrity: str = ""
    duplicate_objects: int = 0
    duplicate_monthly: int = 0
    duplicate_calendar: int = 0
    config: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    object_count: int
    window_hours: int
    spacing_seconds: float
    objects_per_hour_avg: float
    hourly_first_due_counts: dict[int, int]
    min_interval_seconds: float
    max_interval_seconds: float
    avg_interval_seconds: float
    largest_hourly_bucket: int
    full_sweep_hours: float
    max_simultaneous_workers: int = 1


def ingest_source_catalog(
    reader: NotionReader,
    repo: AvailabilityRepository,
    properties: list[PropertySource],
    *,
    now: datetime | None = None,
    writer: object | None = None,
    target_schema: object | None = None,
    target_mapping: object | None = None,
) -> SourceIngestResult:
    """READ source «Аренда недвижимости»; register AIRBNB + FACEBOOK as FIRST_REFRESH."""
    from .models import RefreshStatus

    now = now or _utcnow()
    out = SourceIngestResult()
    for item in properties:
        if not item.object_id:
            continue
        out.scanned += 1
        if item.source == SourceKind.AIRBNB:
            out.airbnb += 1
        elif item.source == SourceKind.FACEBOOK:
            out.facebook += 1
        else:
            out.skipped_unknown += 1
            continue

        existing = repo.get_object(item.object_id)
        if existing is None:
            state = repo.register_new_property(item, now=now)
            out.new_objects.append(state.object_id)
            out.notion_create_planned.append(state.object_id)
            if writer and target_schema and target_mapping:
                try:
                    action, _ = writer.sync_batch_notion_upsert(
                        object_id=state.object_id,
                        object_name=state.name or "",
                        source=state.source,
                        calendar_url=state.source_url or "",
                        batch_allowed_ids=frozenset({state.object_id}),
                        month_cells={},
                        last_checked=None,
                        next_check=state.next_check_at,
                        refresh_status=RefreshStatus.IDLE,
                        last_error="",
                        target_schema=target_schema,
                        target_mapping=target_mapping,
                        refresh_tier=RefreshTier.FIRST_REFRESH,
                        source_status=None,
                    )
                    if action == "created":
                        out.notion_created.append(state.object_id)
                except NotionWriteBlocked:
                    out.notion_write_blocked = True
        else:
            repo.upsert_property(item, default_tier=existing.refresh_tier, now=now)
            out.updated_objects.append(item.object_id)
    return out


def has_established_refresh_schedule(state: AvailabilityObjectState) -> bool:
    """Preserve objects that already completed a successful refresh cycle."""
    if state.next_check_at is None or state.last_checked_at is None:
        return False
    if state.refresh_status == RefreshStatus.SUCCESS:
        return True
    if state.last_calendar_refresh_at is not None:
        return True
    return False


def bootstrap_window_hours_for_count(
    count: int,
    *,
    max_hours: float = PRODUCTION_ROLLING_WINDOW_HOURS,
) -> float:
    """Adaptive window: small fleets spread over minutes, large over up to max_hours."""
    if count <= 1:
        return 0.0
    hours = (count * BOOTSTRAP_SPACING_MINUTES) / 60.0
    return min(float(max_hours), max(0.5, hours))


def bootstrap_candidates(
    objects: list[AvailabilityObjectState],
    *,
    now: datetime,
    source: SourceKind = SourceKind.AIRBNB,
) -> list[AvailabilityObjectState]:
    """Objects that need scheduling without disturbing established SUCCESS rows."""
    subset = [o for o in objects if o.source == source]
    candidates: list[AvailabilityObjectState] = []
    overdue_unestablished: list[AvailabilityObjectState] = []

    for state in subset:
        if has_established_refresh_schedule(state):
            continue
        if state.refresh_tier == RefreshTier.FIRST_REFRESH:
            candidates.append(state)
            continue
        if state.last_checked_at is None:
            overdue_unestablished.append(state)
            continue
        if state.next_check_at is None:
            candidates.append(state)

    if len(overdue_unestablished) >= BURST_DUE_THRESHOLD:
        candidates.extend(overdue_unestablished)
    elif overdue_unestablished:
        candidates.extend(overdue_unestablished)

    seen: set[str] = set()
    unique: list[AvailabilityObjectState] = []
    for state in candidates:
        if state.object_id not in seen:
            seen.add(state.object_id)
            unique.append(state)
    return sorted(unique, key=lambda s: s.object_id)


def bootstrap_spread_selective(
    repo: AvailabilityRepository,
    *,
    now: datetime | None = None,
    source: SourceKind = SourceKind.AIRBNB,
) -> BootstrapSpreadResult:
    """Spread only objects that need bootstrap; preserve established SUCCESS schedules."""
    now = now or _utcnow()
    max_hours = (
        FACEBOOK_ROLLING_WINDOW_HOURS
        if source == SourceKind.FACEBOOK
        else PRODUCTION_ROLLING_WINDOW_HOURS
    )
    objects = repo.list_objects()
    candidates = bootstrap_candidates(objects, now=now, source=source)
    preserved = sum(
        1 for o in objects
        if o.source == source and has_established_refresh_schedule(o)
    )
    out = BootstrapSpreadResult(
        candidates=[c.object_id for c in candidates],
        preserved_count=preserved,
    )
    if not candidates:
        return out

    ids = [c.object_id for c in candidates]
    window_hours = bootstrap_window_hours_for_count(len(ids), max_hours=max_hours)
    out.window_hours = window_hours
    if window_hours <= 0:
        schedule = {ids[0]: now}
    else:
        schedule = spread_next_check_schedule(ids, now=now, window_hours=window_hours)
    out.spacing_minutes = spacing_seconds_for_count(len(ids), window_hours=window_hours) / 60.0
    out.schedule = schedule
    out.largest_hourly_bucket = largest_hourly_bucket(schedule, now=now)
    for oid, nxt in schedule.items():
        repo.set_next_check_at(oid, nxt, now=now)
    out.spread_count = len(schedule)
    return out


def bootstrap_spread_existing(
    repo: AvailabilityRepository,
    *,
    now: datetime | None = None,
    window_hours: int = PRODUCTION_ROLLING_WINDOW_HOURS,
) -> int:
    """Spread next_check_at for all objects across the rolling window."""
    now = now or _utcnow()
    objects = repo.list_objects()
    schedule = spread_next_check_schedule(
        [o.object_id for o in objects],
        now=now,
        window_hours=window_hours,
    )
    for oid, nxt in schedule.items():
        repo.set_next_check_at(oid, nxt, now=now)
    return len(schedule)


def build_due_bucket_preview(
    objects: list[AvailabilityObjectState],
    *,
    now: datetime,
    source: SourceKind | None = None,
    horizon_hours: float = PRODUCTION_ROLLING_WINDOW_HOURS,
) -> DueBucketPreview:
    """Due buckets for scheduler queue (optionally filtered by source)."""
    if source is not None:
        objects = [o for o in objects if o.source == source]
    scheduled = [o for o in objects if o.next_check_at is not None]
    preview = DueBucketPreview()
    times: list[datetime] = []
    for state in scheduled:
        nxt = state.next_check_at
        if nxt is None:
            continue
        times.append(nxt)
        delta = (nxt - now).total_seconds()
        oid = state.object_id
        if delta <= 0:
            preview.now.append(oid)
        elif delta <= 3600:
            preview.next_1h.append(oid)
        elif delta <= 6 * 3600:
            preview.next_6h.append(oid)
        elif delta <= 12 * 3600:
            preview.next_12h.append(oid)
        elif delta <= 24 * 3600:
            preview.next_24h.append(oid)
        elif delta <= horizon_hours * 3600:
            preview.next_48h.append(oid)
    if times:
        preview.earliest = min(times)
        preview.latest = max(times)
    return preview


def largest_hourly_bucket(
    schedule: dict[str, datetime],
    *,
    now: datetime,
) -> int:
    hourly: dict[int, int] = defaultdict(int)
    for nxt in schedule.values():
        hour_bucket = int((nxt - now).total_seconds() // 3600)
        hourly[hour_bucket] += 1
    return max(hourly.values()) if hourly else 0


def backup_sqlite(path: Path, *, now: datetime | None = None) -> Path:
    now = now or _utcnow()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    backup = path.parent / "backups" / f"availability.sqlite3.backup-{stamp}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    return backup


def sqlite_health_checks(repo: AvailabilityRepository) -> tuple[str, int, int, int]:
    integrity = repo._conn.execute("PRAGMA integrity_check").fetchone()[0]
    dup_objects = repo._conn.execute(
        """
        SELECT COUNT(*) - COUNT(DISTINCT object_id) FROM availability_objects
        """
    ).fetchone()[0]
    dup_monthly = repo._conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT object_id, month_key, COUNT(*) AS c
            FROM availability_months GROUP BY object_id, month_key HAVING c > 1
        )
        """
    ).fetchone()[0]
    dup_calendar = repo._conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT object_id, date, COUNT(*) AS c
            FROM availability_calendar_days GROUP BY object_id, date HAVING c > 1
        )
        """
    ).fetchone()[0]
    return str(integrity), int(dup_objects), int(dup_monthly), int(dup_calendar)


def scan_error_backlog(
    objects: list[AvailabilityObjectState],
    *,
    now: datetime,
) -> tuple[list[str], list[str], list[str]]:
    retry_ids: list[str] = []
    error_ids: list[str] = []
    overdue_ids: list[str] = []
    for state in objects:
        if state.source != SourceKind.AIRBNB:
            continue
        if state.retry_count > 0:
            retry_ids.append(state.object_id)
        if state.refresh_status == RefreshStatus.ERROR:
            error_ids.append(state.object_id)
        if state.next_check_at and state.next_check_at < now - timedelta(hours=24):
            overdue_ids.append(state.object_id)
    return retry_ids, error_ids, overdue_ids


def run_scheduler_bootstrap(
    config: AvailabilityConfig | None = None,
    *,
    now: datetime | None = None,
) -> SchedulerBootstrapReport:
    """VPS bootstrap: ingest source, selective spread, preview — no Airbnb/Notion writes."""
    config = config or load_config()
    now = now or _utcnow()
    report = SchedulerBootstrapReport()
    report.config = {
        "default_tier": config.default_refresh_tier.value,
        "object_concurrency": config.object_concurrency,
        "calendar_concurrency": config.calendar_concurrency,
        "price_concurrency": config.price_concurrency,
        "browser_max": config.browser_max_instances,
        "enabled": config.enabled,
        "dry_run": config.dry_run,
        "airbnb_enabled": config.airbnb_enabled,
    }

    reader = NotionReader(config)
    source_schema = reader.fetch_schema(config.source_database_id)
    if source_schema.error:
        raise RuntimeError(f"source schema error: {source_schema.error}")
    mapping = map_source_fields(source_schema.properties)
    properties = reader.read_property_batch(
        config.source_database_id,
        mapping,
        limit=5000,
    )
    report.source_total = len(properties)
    report.source_airbnb = sum(1 for p in properties if p.source == SourceKind.AIRBNB)
    report.source_facebook = sum(1 for p in properties if p.source == SourceKind.FACEBOOK)
    report.source_unknown = sum(1 for p in properties if p.source == SourceKind.UNKNOWN)

    repo = AvailabilityRepository(config.sqlite_path)
    try:
        backup = backup_sqlite(config.sqlite_path, now=now)
        report.sqlite_backup_path = str(backup)

        before_ids = {o.object_id for o in repo.list_objects()}
        report.existing_objects = len(before_ids)
        report.ingest = ingest_source_catalog(reader, repo, properties, now=now)
        report.new_objects = report.ingest.new_objects

        report.spread = bootstrap_spread_selective(repo, now=now, source=SourceKind.AIRBNB)
        report.spread_facebook = bootstrap_spread_selective(repo, now=now, source=SourceKind.FACEBOOK)

        objects = repo.list_objects()
        report.tier_counts = {}
        report.tier_counts_facebook = {}
        for item in objects:
            if item.source == SourceKind.AIRBNB:
                key = item.refresh_tier.value
                report.tier_counts[key] = report.tier_counts.get(key, 0) + 1
            elif item.source == SourceKind.FACEBOOK:
                key = item.refresh_tier.value
                report.tier_counts_facebook[key] = report.tier_counts_facebook.get(key, 0) + 1

        report.retry_objects, report.error_objects, report.overdue_24h = scan_error_backlog(
            objects, now=now
        )
        report.due_airbnb = build_due_bucket_preview(
            objects, now=now, source=SourceKind.AIRBNB
        )
        report.due_facebook = build_due_bucket_preview(
            objects,
            now=now,
            source=SourceKind.FACEBOOK,
            horizon_hours=FACEBOOK_ROLLING_WINDOW_HOURS,
        )

        integrity, dup_o, dup_m, dup_c = sqlite_health_checks(repo)
        report.sqlite_integrity = integrity
        report.duplicate_objects = dup_o
        report.duplicate_monthly = dup_m
        report.duplicate_calendar = dup_c

        report.notes.append("Notion target CREATE planned only (no writes in bootstrap)")
        report.notes.append(
            f"notion_create_planned={len(report.ingest.notion_create_planned)}"
        )
        fb_in_db = sum(1 for o in objects if o.source == SourceKind.FACEBOOK)
        if fb_in_db:
            report.notes.append(f"facebook_rows_in_db={fb_in_db}")
    finally:
        repo.close()

    return report


def print_scheduler_bootstrap_report(report: SchedulerBootstrapReport) -> None:
    print("\n== SOURCE «Аренда недвижимости» (READ ONLY) ==")
    print(f"  total: {report.source_total}")
    print(f"  AIRBNB: {report.source_airbnb}")
    print(f"  FACEBOOK: {report.source_facebook}")
    print(f"  UNKNOWN: {report.source_unknown}")

    print("\n== INGEST ==")
    ing = report.ingest
    print(f"  airbnb: {ing.airbnb}")
    print(f"  facebook: {ing.facebook}")
    print(f"  skipped_unknown: {ing.skipped_unknown}")
    print(f"  new_objects: {len(ing.new_objects)} {ing.new_objects}")
    print(f"  updated_objects: {len(ing.updated_objects)}")
    print(f"  notion_create_planned: {ing.notion_create_planned}")
    print(f"  notion_created: {ing.notion_created}")

    print("\n== SCHEDULER STATE (AIRBNB) ==")
    print(f"  existing_before: {report.existing_objects}")
    print(f"  tiers: {report.tier_counts}")
    print(f"  facebook_tiers: {report.tier_counts_facebook}")
    print(f"  retry_count>0: {len(report.retry_objects)} {report.retry_objects}")
    print(f"  refresh_status=ERROR: {len(report.error_objects)} {report.error_objects}")
    print(f"  overdue>24h: {len(report.overdue_24h)} {report.overdue_24h}")

    sp = report.spread
    print("\n== BOOTSTRAP SPREAD ==")
    print(f"  preserved_established: {sp.preserved_count}")
    print(f"  candidates: {len(sp.candidates)} {sp.candidates}")
    print(f"  spread_count: {sp.spread_count}")
    print(f"  window_hours: {round(sp.window_hours, 2)}")
    print(f"  spacing_minutes: {round(sp.spacing_minutes, 2)}")
    if sp.schedule:
        earliest = min(sp.schedule.values())
        latest = max(sp.schedule.values())
        print(f"  spread_earliest: {earliest.isoformat()}")
        print(f"  spread_latest: {latest.isoformat()}")
        print(f"  largest_hourly_bucket: {sp.largest_hourly_bucket}")

    sp_fb = report.spread_facebook
    print("\n== BOOTSTRAP SPREAD (FACEBOOK) ==")
    print(f"  preserved_established: {sp_fb.preserved_count}")
    print(f"  candidates: {len(sp_fb.candidates)} {sp_fb.candidates}")
    print(f"  spread_count: {sp_fb.spread_count}")
    print(f"  window_hours: {round(sp_fb.window_hours, 2)}")

    due_fb = report.due_facebook
    print("\n== DUE PREVIEW (FACEBOOK) ==")
    print(f"  NOW DUE ({len(due_fb.now)}): {due_fb.now}")
    print(f"  NEXT 1H ({len(due_fb.next_1h)}): {due_fb.next_1h}")
    print(f"  NEXT 6H ({len(due_fb.next_6h)}): {due_fb.next_6h}")
    print(f"  NEXT 12H ({len(due_fb.next_12h)}): {due_fb.next_12h}")
    print(f"  NEXT 24H ({len(due_fb.next_24h)}): {due_fb.next_24h}")
    print(f"  NEXT 78H ({len(due_fb.next_48h)}): {due_fb.next_48h}")

    due = report.due_airbnb
    print("\n== DUE PREVIEW (AIRBNB only) ==")
    print(f"  NOW DUE ({len(due.now)}): {due.now}")
    print(f"  NEXT 1H ({len(due.next_1h)}): {due.next_1h}")
    print(f"  NEXT 6H ({len(due.next_6h)}): {due.next_6h}")
    print(f"  NEXT 12H ({len(due.next_12h)}): {due.next_12h}")
    print(f"  NEXT 24H ({len(due.next_24h)}): {due.next_24h}")
    print(f"  NEXT 48H ({len(due.next_48h)}): {due.next_48h}")
    if due.earliest:
        print(f"  earliest next_check_at: {due.earliest.isoformat()}")
    if due.latest:
        print(f"  latest next_check_at: {due.latest.isoformat()}")

    print("\n== SQLITE ==")
    print(f"  backup: {report.sqlite_backup_path}")
    print(f"  integrity_check: {report.sqlite_integrity}")
    print(f"  duplicate_objects: {report.duplicate_objects}")
    print(f"  duplicate_monthly: {report.duplicate_monthly}")
    print(f"  duplicate_calendar: {report.duplicate_calendar}")

    print("\n== PRODUCTION CONFIG ==")
    c = report.config
    print(f"  default_tier: {c['default_tier']}")
    print(
        f"  concurrency: object={c['object_concurrency']} calendar={c['calendar_concurrency']} "
        f"price={c['price_concurrency']} browser_max={c['browser_max']}"
    )
    print(f"  enabled={c['enabled']} dry_run={c['dry_run']} airbnb_enabled={c['airbnb_enabled']}")

    print("\n== GO-LIVE (NOT RUN) ==")
    print("  CLI:")
    print("    cd /opt/openhome/app")
    print("    /opt/openhome/venv/bin/python3 -m availability_service.main scheduler-loop")
    print("  systemd ExecStart (future):")
    print("    /opt/openhome/venv/bin/python3 -m availability_service.main scheduler-loop")
    print("  required env for go-live:")
    print("    AVAILABILITY_ENABLED=true")
    print("    AVAILABILITY_DRY_RUN=false")
    print("    AVAILABILITY_AIRBNB_ENABLED=true")
    print("    AVAILABILITY_FACEBOOK_ENABLED=true")
    print("    AVAILABILITY_DEFAULT_REFRESH_TIER=48H")
    print("    AVAILABILITY_OBJECT_CONCURRENCY=1")
    print("    AVAILABILITY_CALENDAR_CONCURRENCY=1")
    print("    AVAILABILITY_PRICE_CONCURRENCY=1")
    print("    AVAILABILITY_BROWSER_MAX_INSTANCES=2")

    print("\n== SAFETY ==")
    print("  Airbnb requests = 0")
    print("  Notion source writes = 0")
    print("  scheduler loop OFF")
    print("  systemd OFF")
    for note in report.notes:
        print(f"  {note}")


def simulate_rolling_window(
    object_count: int,
    *,
    window_hours: int = PRODUCTION_ROLLING_WINDOW_HOURS,
    now: datetime | None = None,
) -> SimulationResult:
    """Dry-run: model initial due spread for N objects (no live processing)."""
    now = now or _utcnow()
    ids = [f"SIM_{i:04d}" for i in range(object_count)]
    schedule = spread_next_check_schedule(ids, now=now, window_hours=window_hours)
    spacing = spacing_seconds_for_count(object_count, window_hours=window_hours)

    hourly: dict[int, int] = defaultdict(int)
    intervals: list[float] = []
    sorted_times = sorted(schedule.values())
    for nxt in sorted_times:
        hour_bucket = int((nxt - now).total_seconds() // 3600)
        hourly[hour_bucket] += 1
    for i in range(1, len(sorted_times)):
        intervals.append((sorted_times[i] - sorted_times[i - 1]).total_seconds())

    largest_bucket = max(hourly.values()) if hourly else 0
    avg_per_hour = object_count / window_hours if window_hours else 0.0
    full_sweep = window_hours
    if sorted_times:
        full_sweep = (sorted_times[-1] - now).total_seconds() / 3600.0

    return SimulationResult(
        object_count=object_count,
        window_hours=window_hours,
        spacing_seconds=spacing,
        objects_per_hour_avg=avg_per_hour,
        hourly_first_due_counts=dict(hourly),
        min_interval_seconds=min(intervals) if intervals else 0.0,
        max_interval_seconds=max(intervals) if intervals else 0.0,
        avg_interval_seconds=(sum(intervals) / len(intervals)) if intervals else 0.0,
        largest_hourly_bucket=largest_bucket,
        full_sweep_hours=full_sweep,
    )


def run_scheduler_simulation(
    object_count: int = 1000,
    *,
    window_hours: int = PRODUCTION_ROLLING_WINDOW_HOURS,
) -> SimulationResult:
    return simulate_rolling_window(object_count, window_hours=window_hours)


def run_scheduler_dry_run(config: AvailabilityConfig | None = None) -> dict:
    """Preview scheduler state + 1000-object simulation (no live refresh)."""
    config = config or load_config()
    repo = AvailabilityRepository(config.sqlite_path)
    try:
        scheduler = AvailabilityScheduler(repo)
        preview = scheduler.preview()
        sim = run_scheduler_simulation(1000, window_hours=PRODUCTION_ROLLING_WINDOW_HOURS)
        return {
            "preview": preview,
            "simulation": {
                "object_count": sim.object_count,
                "window_hours": sim.window_hours,
                "spacing_seconds": round(sim.spacing_seconds, 1),
                "spacing_minutes": round(sim.spacing_seconds / 60, 2),
                "objects_per_hour_avg": round(sim.objects_per_hour_avg, 2),
                "target_objects_per_hour": TARGET_OBJECTS_PER_HOUR,
                "largest_hourly_bucket": sim.largest_hourly_bucket,
                "full_sweep_hours": round(sim.full_sweep_hours, 2),
                "min_interval_minutes": round(sim.min_interval_seconds / 60, 2),
                "max_interval_minutes": round(sim.max_interval_seconds / 60, 2),
                "avg_interval_minutes": round(sim.avg_interval_seconds / 60, 2),
                "max_simultaneous_workers": sim.max_simultaneous_workers,
            },
            "concurrency": {
                "object": config.object_concurrency,
                "calendar": config.calendar_concurrency,
                "price": config.price_concurrency,
                "browser_max": config.browser_max_instances,
            },
            "default_tier": config.default_refresh_tier.value,
        }
    finally:
        repo.close()


@dataclass
class ProductionGoLiveReport:
    source_total: int = 0
    source_airbnb: int = 0
    source_facebook: int = 0
    source_unknown: int = 0
    ingest: SourceIngestResult = field(default_factory=SourceIngestResult)
    legacy_migrated_to_prod: list[str] = field(default_factory=list)
    legacy_migrated_to_first: list[str] = field(default_factory=list)
    notion_created: list[str] = field(default_factory=list)
    notion_updated: list[str] = field(default_factory=list)
    notion_duplicate_blocked: list[str] = field(default_factory=list)
    spread: BootstrapSpreadResult = field(default_factory=BootstrapSpreadResult)
    spread_facebook: BootstrapSpreadResult = field(default_factory=BootstrapSpreadResult)
    tier_counts: dict[str, int] = field(default_factory=dict)
    tier_counts_facebook: dict[str, int] = field(default_factory=dict)
    legacy_12h_remaining: int = 0
    sqlite_backup_path: str = ""
    sqlite_integrity: str = ""
    duplicate_objects: int = 0
    notes: list[str] = field(default_factory=list)


def migrate_legacy_scheduler_tiers(
    repo: AvailabilityRepository,
    *,
    now: datetime | None = None,
) -> tuple[list[str], list[str]]:
    """Migrate legacy 12H tiers to 48H/78H (established) or FIRST_REFRESH (unestablished)."""
    from .scheduler_policy import production_tier_for_source

    now = now or _utcnow()
    to_prod: list[str] = []
    to_first: list[str] = []
    for state in repo.list_objects():
        if state.refresh_tier != RefreshTier.H12:
            continue
        if has_established_refresh_schedule(state):
            tier = production_tier_for_source(state.source)
            repo.set_refresh_tier(state.object_id, tier, now=now)
            to_prod.append(state.object_id)
        else:
            repo.set_refresh_tier(state.object_id, RefreshTier.FIRST_REFRESH, now=now)
            if state.next_check_at is None:
                repo.set_next_check_at(state.object_id, now, now=now)
            to_first.append(state.object_id)
    return to_prod, to_first


def ensure_notion_target_rows(
    repo: AvailabilityRepository,
    writer: object,
    target_schema: object,
    target_mapping: object,
    *,
    now: datetime | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Create or link missing target rows; skip ambiguous duplicates."""
    from .notion_writer import NotionWriter, SyncOneWriteRefused

    now = now or _utcnow()
    if not isinstance(writer, NotionWriter):
        return [], [], []

    created: list[str] = []
    updated: list[str] = []
    duplicate_blocked: list[str] = []

    for state in repo.list_objects():
        if state.source not in (SourceKind.AIRBNB, SourceKind.FACEBOOK):
            continue
        pages = writer.find_target_pages(
            target_schema.database_id,
            target_mapping,
            state.object_id,
            target_schema.properties,
        )
        if len(pages) > 1:
            duplicate_blocked.append(state.object_id)
            continue
        if pages:
            page_id = str(pages[0].get("id") or "")
            if page_id and page_id != state.notion_page_id:
                repo.set_notion_page_id(state.object_id, page_id, now=now)
                updated.append(state.object_id)
            continue
        tier = (
            RefreshTier.H78
            if state.source == SourceKind.FACEBOOK
            else state.refresh_tier
        )
        try:
            action, page_id = writer.sync_batch_notion_upsert(
                object_id=state.object_id,
                object_name=state.name or "",
                source=state.source,
                calendar_url=state.source_url or "",
                batch_allowed_ids=frozenset({state.object_id}),
                month_cells={},
                last_checked=state.last_checked_at,
                next_check=state.next_check_at,
                refresh_status=state.refresh_status,
                last_error=state.last_error or "",
                target_schema=target_schema,
                target_mapping=target_mapping,
                refresh_tier=tier,
                source_status=state.source_status,
            )
            if page_id:
                repo.set_notion_page_id(state.object_id, page_id, now=now)
            if action == "created":
                created.append(state.object_id)
            else:
                updated.append(state.object_id)
        except SyncOneWriteRefused as exc:
            duplicate_blocked.append(f"{state.object_id}: {exc}")
        except NotionWriteBlocked:
            break
    return created, updated, duplicate_blocked


def run_production_go_live(
    config: AvailabilityConfig | None = None,
    *,
    now: datetime | None = None,
) -> ProductionGoLiveReport:
    """Live bootstrap: ingest, legacy migration, Notion upsert, selective spread."""
    from .notion_reader import map_target_fields
    from .notion_writer import NotionWriter

    config = config or load_config()
    now = now or _utcnow()
    if not config.writes_allowed:
        raise SystemExit(
            "REFUSED: AVAILABILITY_ENABLED=true and AVAILABILITY_DRY_RUN=false required"
        )
    if not config.notion_api_key:
        raise SystemExit("NOTION_API_KEY missing")

    report = ProductionGoLiveReport()
    reader = NotionReader(config)
    writer = NotionWriter(config)
    target_schema = reader.fetch_schema(config.target_database_id)
    if target_schema.error:
        raise RuntimeError(f"target schema error: {target_schema.error}")
    target_mapping = map_target_fields(target_schema.properties)

    source_schema = reader.fetch_schema(config.source_database_id)
    if source_schema.error:
        raise RuntimeError(f"source schema error: {source_schema.error}")
    mapping = map_source_fields(source_schema.properties)
    properties = reader.read_property_batch(
        config.source_database_id,
        mapping,
        limit=5000,
    )
    report.source_total = len(properties)
    report.source_airbnb = sum(1 for p in properties if p.source == SourceKind.AIRBNB)
    report.source_facebook = sum(1 for p in properties if p.source == SourceKind.FACEBOOK)
    report.source_unknown = sum(1 for p in properties if p.source == SourceKind.UNKNOWN)

    repo = AvailabilityRepository(config.sqlite_path)
    try:
        backup = backup_sqlite(config.sqlite_path, now=now)
        report.sqlite_backup_path = str(backup)

        report.ingest = ingest_source_catalog(
            reader,
            repo,
            properties,
            now=now,
            writer=writer,
            target_schema=target_schema,
            target_mapping=target_mapping,
        )
        report.legacy_migrated_to_prod, report.legacy_migrated_to_first = (
            migrate_legacy_scheduler_tiers(repo, now=now)
        )
        created, linked, dupes = ensure_notion_target_rows(
            repo,
            writer,
            target_schema,
            target_mapping,
            now=now,
        )
        report.notion_created = created
        report.notion_updated = linked
        report.notion_duplicate_blocked = dupes

        report.spread = bootstrap_spread_selective(repo, now=now, source=SourceKind.AIRBNB)
        report.spread_facebook = bootstrap_spread_selective(
            repo, now=now, source=SourceKind.FACEBOOK
        )

        objects = repo.list_objects()
        for item in objects:
            if item.source == SourceKind.AIRBNB:
                key = item.refresh_tier.value
                report.tier_counts[key] = report.tier_counts.get(key, 0) + 1
            elif item.source == SourceKind.FACEBOOK:
                key = item.refresh_tier.value
                report.tier_counts_facebook[key] = report.tier_counts.get(key, 0) + 1

        report.legacy_12h_remaining = sum(
            1 for o in objects if o.refresh_tier == RefreshTier.H12
        )
        integrity, dup_o, dup_m, dup_c = sqlite_health_checks(repo)
        report.sqlite_integrity = integrity
        report.duplicate_objects = dup_o
        if dup_m or dup_c:
            report.notes.append(f"duplicate_monthly={dup_m} duplicate_calendar={dup_c}")
    finally:
        repo.close()

    return report


def print_production_go_live_report(report: ProductionGoLiveReport) -> None:
    print("\n== PRODUCTION GO-LIVE ==")
    print(f"  source_total: {report.source_total}")
    print(f"  AIRBNB: {report.source_airbnb}")
    print(f"  FACEBOOK: {report.source_facebook}")
    print(f"  UNKNOWN: {report.source_unknown}")
    ing = report.ingest
    print(f"  ingest_new: {ing.new_objects}")
    print(f"  ingest_notion_created: {ing.notion_created}")
    print(f"  legacy→prod: {report.legacy_migrated_to_prod}")
    print(f"  legacy→first: {report.legacy_migrated_to_first}")
    print(f"  notion_created: {report.notion_created}")
    print(f"  notion_linked: {report.notion_updated}")
    print(f"  notion_duplicate_blocked: {report.notion_duplicate_blocked}")
    print(f"  spread_airbnb: {report.spread.spread_count}")
    print(f"  spread_facebook: {report.spread_facebook.spread_count}")
    print(f"  tier_counts_airbnb: {report.tier_counts}")
    print(f"  tier_counts_facebook: {report.tier_counts_facebook}")
    print(f"  legacy_12h_remaining: {report.legacy_12h_remaining}")
    print(f"  sqlite_integrity: {report.sqlite_integrity}")
    print(f"  duplicate_objects: {report.duplicate_objects}")
    print(f"  sqlite_backup: {report.sqlite_backup_path}")
    if report.notes:
        print("  notes:")
        for note in report.notes:
            print(f"    - {note}")


def print_scheduler_simulation_report(result: dict) -> None:
    print("\n== PRODUCTION SCHEDULER DRY-RUN ==")
    print(f"  default_tier: {result['default_tier']}")
    c = result["concurrency"]
    print(
        f"  concurrency: object={c['object']} calendar={c['calendar']} "
        f"price={c['price']} browser_max={c['browser_max']}"
    )
    sim = result["simulation"]
    print("\n== 1000-OBJECT SIMULATION ==")
    print(f"  spacing: {sim['spacing_minutes']} min (~{sim['spacing_seconds']}s)")
    print(f"  objects/hour (avg): {sim['objects_per_hour_avg']}")
    print(f"  target objects/hour: {sim['target_objects_per_hour']}")
    print(f"  largest hourly bucket: {sim['largest_hourly_bucket']}")
    print(f"  full sweep window: {sim['full_sweep_hours']} h")
    print(f"  interval min/max/avg (min): {sim['min_interval_minutes']}/{sim['max_interval_minutes']}/{sim['avg_interval_minutes']}")
    print(f"  max simultaneous workers: {sim['max_simultaneous_workers']}")
    preview = result["preview"]
    print("\n== CURRENT DB PREVIEW ==")
    print(f"  total_objects: {preview['total_objects']}")
    print(f"  due_now: {preview['due']}")
    print(f"  tiers: {preview['tiers']}")
    print("\n== SAFETY ==")
    print("  scheduler live OFF")
    print("  systemd OFF")
    print("  permanent LIVE flags unchanged")
