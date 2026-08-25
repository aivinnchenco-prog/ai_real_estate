from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import (
    AvailabilityObjectState,
    RefreshJob,
    RefreshTier,
    SourceKind,
    TIER_HOURS,
)
from .repository import AvailabilityRepository
from .scheduler_policy import (
    compute_retry_next_check_at,
    MAX_SIMULTANEOUS_OBJECT_WORKERS,
    production_tier_for_source,
    retry_fallback_tier,
    success_interval_hours,
    tier_after_success,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compute_next_check_at(
    *,
    now: datetime | None = None,
    tier: RefreshTier = RefreshTier.H48,
    from_time: datetime | None = None,
) -> datetime:
    now = now or _utcnow()
    base = from_time or now
    hours = success_interval_hours(tier)
    return base + timedelta(hours=hours)


def select_due_objects(
    objects: list[AvailabilityObjectState],
    *,
    now: datetime | None = None,
) -> list[AvailabilityObjectState]:
    now = now or _utcnow()
    due = [
        item for item in objects
        if item.next_check_at is not None and item.next_check_at <= now
    ]
    return sorted(due, key=lambda item: (item.next_check_at or now, item.object_id))


class AvailabilityScheduler:
    """Per-object next_check_at scheduler. Rolling 48H — never a global sweep."""

    def __init__(self, repository: AvailabilityRepository) -> None:
        self.repository = repository

    def due_objects(self, *, now: datetime | None = None) -> list[AvailabilityObjectState]:
        return self.repository.due_objects(now=now)

    def would_enqueue_due(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[int, list[str]]:
        """Preview enqueue without creating refresh_jobs."""
        now = now or _utcnow()
        due = self.due_objects(now=now)
        would: list[str] = []
        for item in due:
            if self.repository.active_job(item.object_id) is None:
                would.append(item.object_id)
        if limit is not None:
            would = would[:limit]
        return len(would), would

    def enqueue_due(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[tuple[RefreshJob, bool]]:
        now = now or _utcnow()
        due = self.due_objects(now=now)
        if limit is not None:
            due = due[:limit]
        results: list[tuple[RefreshJob, bool]] = []
        for item in due:
            results.append(self.repository.enqueue_job(item.object_id, now=now))
        return results

    def enqueue_next_due(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[RefreshJob, bool] | None:
        """Production: one object at a time (object_concurrency=1)."""
        results = self.enqueue_due(now=now, limit=MAX_SIMULTANEOUS_OBJECT_WORKERS)
        return results[0] if results else None

    def schedule_success(
        self,
        object_id: str,
        *,
        now: datetime | None = None,
        tier: RefreshTier | None = None,
    ) -> datetime:
        now = now or _utcnow()
        state = self.repository.get_object(object_id)
        if state is None:
            raise KeyError(object_id)
        effective_tier = tier_after_success(state.refresh_tier, state.source)
        if tier is not None:
            effective_tier = tier
        if state.refresh_tier != effective_tier:
            state.refresh_tier = effective_tier
            self.repository.save_object(state, now=now)
        nxt = compute_next_check_at(now=now, tier=effective_tier, from_time=now)
        self.repository.mark_job_success(object_id, next_check_at=nxt, now=now)
        return nxt

    def schedule_retry(
        self,
        object_id: str,
        error: str,
        *,
        now: datetime | None = None,
        tier: RefreshTier | None = None,
    ) -> tuple[datetime, int]:
        now = now or _utcnow()
        state = self.repository.get_object(object_id)
        if state is None:
            raise KeyError(object_id)
        fallback = tier or state.refresh_tier
        if fallback == RefreshTier.FIRST_REFRESH:
            fallback = retry_fallback_tier(state.source)
        nxt = compute_retry_next_check_at(
            now=now,
            retry_count=state.retry_count,
            fallback_tier=fallback,
        )
        retry_count = self.repository.mark_job_error(
            object_id, error, next_check_at=nxt, now=now
        )
        return nxt, retry_count

    def preview(self, *, now: datetime | None = None) -> dict:
        now = now or _utcnow()
        objects = self.repository.list_objects()
        due = select_due_objects(objects, now=now)
        by_tier: dict[str, int] = {}
        for item in objects:
            key = item.refresh_tier.value
            by_tier[key] = by_tier.get(key, 0) + 1
        return {
            "now": now.isoformat(),
            "total_objects": len(objects),
            "due": len(due),
            "due_object_ids": [item.object_id for item in due],
            "tiers": by_tier,
            "sample": [
                {
                    "object_id": item.object_id,
                    "tier": item.refresh_tier.value,
                    "next_check_at": item.next_check_at.isoformat() if item.next_check_at else None,
                    "refresh_status": item.refresh_status.value,
                }
                for item in due[:10]
            ],
        }
