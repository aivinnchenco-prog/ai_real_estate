from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..models import AvailabilityObjectState, SourceKind
from ..repository import AvailabilityRepository
from .config import AirbnbWorkerPoolConfig
from .pool import AirbnbWorkerPool
from .registry import AirbnbWorkerRepository


@dataclass
class WorkerJob:
    object_id: str
    worker_id: str
    state: AvailabilityObjectState


def pick_airbnb_worker_jobs(
    due_objects: list[AvailabilityObjectState],
    pool: AirbnbWorkerPool,
    worker_repo: AirbnbWorkerRepository,
    config: AirbnbWorkerPoolConfig,
    *,
    now: datetime,
) -> list[WorkerJob]:
    """One due object per eligible worker (respecting per-worker concurrency)."""
    airbnb_due = [
        o for o in due_objects if o.source == SourceKind.AIRBNB or o.object_id.startswith("A_")
    ]
    if not airbnb_due:
        return []

    eligible_workers = {
        w.worker_id: w for w in pool.workers_eligible_for_jobs(now=now)
    }
    if not eligible_workers:
        return []

    by_worker: dict[str, list[AvailabilityObjectState]] = {}
    for item in airbnb_due:
        assignment = worker_repo.get_assignment(item.object_id)
        if assignment is None:
            try:
                worker = pool.ensure_assignment(item.object_id, now=now)
                worker_id = worker.worker_id
            except RuntimeError:
                continue
        else:
            worker_id = assignment.worker_id
        if worker_id not in eligible_workers:
            continue
        worker = eligible_workers[worker_id]
        if worker.active_jobs >= config.max_concurrency:
            continue
        by_worker.setdefault(worker_id, []).append(item)

    jobs: list[WorkerJob] = []
    for worker_id, items in sorted(by_worker.items()):
        items.sort(key=lambda o: (o.next_check_at or now, o.object_id))
        chosen = items[0]
        jobs.append(
            WorkerJob(object_id=chosen.object_id, worker_id=worker_id, state=chosen)
        )
    jobs.sort(key=lambda j: (j.state.next_check_at or now, j.object_id))
    return jobs
