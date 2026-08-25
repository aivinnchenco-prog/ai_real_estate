"""CLI entrypoints for Airbnb worker pool management."""

from __future__ import annotations

from ..repository import AvailabilityRepository
from .config import load_worker_pool_config
from .diagnostics import (
    build_diagnostics,
    print_diagnostics,
    print_migration_dry_run,
    run_migration_dry_run,
)
from .pool import AirbnbWorkerPool
from .registry import AirbnbWorkerRepository


def run_airbnb_workers_status(repo: AvailabilityRepository) -> None:
    report = build_diagnostics(repo)
    print_diagnostics(report)


def run_airbnb_workers_bootstrap(repo: AvailabilityRepository) -> int:
    pool_config = load_worker_pool_config()
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, pool_config)
    created = pool.bootstrap()
    assigned = pool.assign_new_objects()
    print(f"bootstrap: created={len(created)} new_assignments={assigned}")
    run_airbnb_workers_status(repo)
    return len(created)


def run_airbnb_workers_migrate(
    repo: AvailabilityRepository,
    *,
    dry_run: bool = False,
) -> dict[str, str]:
    pool_config = load_worker_pool_config()
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, pool_config)
    pool.bootstrap()
    assignments = pool.migrate_balanced(dry_run=dry_run)
    if dry_run:
        print_migration_dry_run(assignments)
    else:
        print(f"migration complete: {len(assignments)} objects assigned")
        run_airbnb_workers_status(repo)
    return assignments
