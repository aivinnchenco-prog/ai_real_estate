from __future__ import annotations

from dataclasses import dataclass, field

from ..repository import AvailabilityRepository
from .config import AirbnbWorkerPoolConfig, load_worker_pool_config
from .pool import AirbnbWorkerPool
from .registry import AirbnbWorkerRepository
from .worker_paths import pricing_profile_path


@dataclass
class WorkerDiagnosticsReport:
    summary: dict = field(default_factory=dict)
    workers: list[dict] = field(default_factory=list)
    assignments: dict[str, str] = field(default_factory=dict)
    config: dict = field(default_factory=dict)


def build_diagnostics(repo: AvailabilityRepository) -> WorkerDiagnosticsReport:
    pool_config = load_worker_pool_config()
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, pool_config)
    pool.bootstrap()
    pool.assign_new_objects()

    report = WorkerDiagnosticsReport()
    report.summary = pool.load_summary()
    report.config = {
        "enabled": pool_config.enabled,
        "pricing_worker_pool_enabled": pool_config.pricing_worker_pool_enabled,
        "max_concurrency": pool_config.max_concurrency,
        "min_delay_seconds": pool_config.min_delay_seconds,
        "max_delay_seconds": pool_config.max_delay_seconds,
        "pricing_min_delay_seconds": pool_config.pricing_min_delay_seconds,
        "pricing_max_delay_seconds": pool_config.pricing_max_delay_seconds,
        "rebalance_threshold": pool_config.rebalance_threshold,
        "captcha_cooldown_minutes": pool_config.captcha_cooldown_minutes,
        "profiles_root": str(pool_config.profiles_root),
        "proxy_pool_path": str(pool_config.proxy_pool_path),
    }
    for worker in worker_repo.list_workers():
        report.workers.append(
            {
                "worker_id": worker.worker_id,
                "status": worker.status.value,
                "proxy_id": worker.proxy_id,
                "proxy_endpoint": worker.proxy_endpoint.split("@")[-1],
                "objects": worker.assigned_count,
                "active_jobs": worker.active_jobs,
                "last_calendar_success_at": worker.last_success_at.isoformat()
                if worker.last_success_at
                else None,
                "last_calendar_failure_at": worker.last_failure_at.isoformat()
                if worker.last_failure_at
                else None,
                "last_pricing_success_at": worker.last_pricing_success_at.isoformat()
                if worker.last_pricing_success_at
                else None,
                "last_pricing_failure_at": worker.last_pricing_failure_at.isoformat()
                if worker.last_pricing_failure_at
                else None,
                "last_pricing_error": worker.last_pricing_error or None,
                "consecutive_failures": worker.consecutive_failures,
                "captcha_count": worker.captcha_count,
                "cooldown_until": worker.cooldown_until.isoformat() if worker.cooldown_until else None,
                "user_agent_prefix": worker.user_agent[:60] + "...",
                "profile_path": worker.profile_path,
                "pricing_profile_path": str(pricing_profile_path(worker)),
            }
        )
    for assignment in worker_repo.list_assignments():
        report.assignments[assignment.object_id] = assignment.worker_id
    return report


def print_diagnostics(report: WorkerDiagnosticsReport) -> None:
    print("\n== AIRBNB WORKER POOL ==")
    s = report.summary
    print(f"  workers total: {s.get('total_workers', 0)}")
    print(f"  ACTIVE: {s.get('active', 0)}")
    print(f"  COOLDOWN: {s.get('cooldown', 0)}")
    print(f"  QUARANTINED: {s.get('quarantined', 0)}")
    print(f"  DISABLED: {s.get('disabled', 0)}")
    print(f"  Airbnb objects: {s.get('airbnb_objects', 0)}")
    print(f"  unassigned: {s.get('unassigned', 0)}")
    print(f"  min load: {s.get('min_load', 0)}")
    print(f"  max load: {s.get('max_load', 0)}")
    print(f"  load spread: {s.get('spread', 0)}")

    print("\n== CONFIG ==")
    for key, value in report.config.items():
        print(f"  {key}: {value}")

    print("\n== WORKERS ==")
    for w in report.workers:
        print(f"  {w['worker_id']}")
        print(f"    status: {w['status']}")
        print(f"    proxy_id: {w['proxy_id']}")
        print(f"    objects: {w['objects']}")
        print(f"    active_jobs: {w['active_jobs']}")
        print(f"    captcha: {w['captcha_count']}")
        if w["cooldown_until"]:
            print(f"    cooldown_until: {w['cooldown_until']}")
        if w.get("last_calendar_success_at"):
            print(f"    calendar_last_success: {w['last_calendar_success_at']}")
        if w.get("last_pricing_success_at"):
            print(f"    pricing_last_success: {w['last_pricing_success_at']}")
        if w.get("last_pricing_failure_at"):
            print(f"    pricing_last_failure: {w['last_pricing_failure_at']}")
        if w.get("last_pricing_error"):
            print(f"    pricing_last_error: {w['last_pricing_error']}")
        if w.get("pricing_profile_path"):
            print(f"    pricing_profile: {w['pricing_profile_path']}")

    if report.assignments:
        print("\n== ASSIGNMENT SAMPLE (first 20) ==")
        for oid, wid in sorted(report.assignments.items())[:20]:
            print(f"  {oid} → {wid}")
        if len(report.assignments) > 20:
            print(f"  ... and {len(report.assignments) - 20} more")


def run_migration_dry_run(repo: AvailabilityRepository) -> dict[str, str]:
    pool_config = load_worker_pool_config()
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, pool_config)
    pool.bootstrap()
    return pool.migrate_balanced(dry_run=True)


def print_migration_dry_run(assignments: dict[str, str]) -> None:
    by_worker: dict[str, list[str]] = {}
    for oid, wid in sorted(assignments.items()):
        by_worker.setdefault(wid, []).append(oid)
    print("\n== AIRBNB WORKER MIGRATION DRY RUN ==")
    print(f"  Airbnb objects: {len(assignments)}")
    print(f"  Workers: {len(by_worker)}")
    loads = [len(v) for v in by_worker.values()]
    print(f"  Unassigned: 0")
    if loads:
        print(f"  Load spread: min={min(loads)} max={max(loads)} diff={max(loads)-min(loads)}")
    print("\n== PER WORKER ==")
    for wid in sorted(by_worker):
        objs = by_worker[wid]
        print(f"  {wid} → {len(objs)} objects")
    print("\n== OBJECT → WORKER ==")
    for oid, wid in sorted(assignments.items()):
        print(f"  {oid} → {wid}")
