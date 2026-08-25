"""Cross-worker calendar diagnostics (read-only, no Notion writes)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..repository import AvailabilityRepository
from .calendar_fetch import normalize_listing_url, run_calendar_trace
from .config import load_worker_pool_config
from .integration import fetch_airbnb_calendar_for_object
from .pool import AirbnbWorkerPool
from .registry import AirbnbWorkerRepository


@dataclass
class TraceMatrixRow:
    object_id: str
    worker_id: str
    status: str
    message: str
    page_kind: str = ""
    final_url: str = ""
    title: str = ""
    days: int = 0
    diagnostic: dict = field(default_factory=dict)


def _resolve_listing_url(repo: AvailabilityRepository, object_id: str, listing_url: str | None) -> str:
    if listing_url:
        return normalize_listing_url(listing_url)
    # fallback: notion reader path not wired here; caller must pass URL
    raise ValueError(f"listing_url required for {object_id}")


def run_trace_matrix(
    repo: AvailabilityRepository,
    *,
    cases: list[tuple[str, str, str | None]],
) -> list[TraceMatrixRow]:
    """cases: (object_id, worker_id, listing_url or None)."""
    pool_config = load_worker_pool_config()
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, pool_config)
    pool.bootstrap()
    rows: list[TraceMatrixRow] = []
    for object_id, worker_id, listing_url in cases:
        worker = worker_repo.get_worker(worker_id)
        if worker is None:
            rows.append(
                TraceMatrixRow(object_id, worker_id, "ERROR", f"worker {worker_id} missing")
            )
            continue
        proxy = pool.get_proxy(worker.proxy_id)
        if proxy is None:
            rows.append(
                TraceMatrixRow(object_id, worker_id, "ERROR", f"proxy missing for {worker_id}")
            )
            continue
        url = _resolve_listing_url(repo, object_id, listing_url)
        result = run_calendar_trace(worker, proxy, url, object_id=object_id)
        diag = result.diagnostic or {}
        rows.append(
            TraceMatrixRow(
                object_id=object_id,
                worker_id=worker_id,
                status=result.status.value,
                message=result.message,
                page_kind=str(diag.get("page_kind") or diag.get("page_kind", "")),
                final_url=str(diag.get("final_url") or ""),
                title=str(diag.get("page_title") or ""),
                days=len(result.days),
                diagnostic=diag,
            )
        )
    return rows


def print_trace_matrix(rows: list[TraceMatrixRow]) -> None:
    print("\n== CALENDAR TRACE MATRIX ==")
    for row in rows:
        print(f"\n{row.object_id} @ {row.worker_id}")
        print(f"  status: {row.status}")
        print(f"  page_kind: {row.page_kind}")
        print(f"  message: {row.message}")
        print(f"  final_url: {row.final_url[:120]}")
        print(f"  title: {row.title[:100]}")
        print(f"  days: {row.days}")
        events = row.diagnostic.get("network_events") or []
        if events:
            print("  network:")
            for ev in events[:8]:
                print(
                    f"    - {ev.get('name')} status={ev.get('status')} "
                    f"matched={ev.get('matched_calendar')}"
                )


def run_controlled_smoke(
    repo: AvailabilityRepository,
    cases: list[tuple[str, str | None]],
) -> list[TraceMatrixRow]:
    """Smoke via assigned worker (ensure_assignment) — no Notion writes."""
    pool_config = load_worker_pool_config()
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, pool_config)
    pool.bootstrap()
    rows: list[TraceMatrixRow] = []
    for object_id, listing_url in cases:
        prop_url = listing_url
        if not prop_url:
            raise ValueError(f"listing_url required for smoke {object_id}")
        assigned = pool.ensure_assignment(object_id)
        result = fetch_airbnb_calendar_for_object(
            repo,
            object_id,
            prop_url,
            pool=pool,
            pool_config=pool_config,
        )
        diag = result.diagnostic or {}
        rows.append(
            TraceMatrixRow(
                object_id=object_id,
                worker_id=assigned.worker_id,
                status=result.status.value,
                message=result.message,
                page_kind=str(diag.get("page_kind") or ""),
                final_url=str(diag.get("final_url") or ""),
                title=str(diag.get("page_title") or ""),
                days=len(result.days),
                diagnostic=diag,
            )
        )
    return rows
