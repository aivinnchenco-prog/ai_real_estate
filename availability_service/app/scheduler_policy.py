"""Production rolling scheduler policy (48H window, FIRST_REFRESH, bounded retries)."""
from __future__ import annotations

from datetime import datetime, timedelta

from .models import RefreshTier, SourceKind, TIER_HOURS

PRODUCTION_ROLLING_WINDOW_HOURS = 48
FACEBOOK_ROLLING_WINDOW_HOURS = 78
TARGET_OBJECTS_PER_HOUR = 21
MAX_SIMULTANEOUS_OBJECT_WORKERS = 1
FACEBOOK_MAX_SIMULTANEOUS_WORKERS = 1

# Minutes between retry attempts after ERROR (not full 48H).
RETRY_DELAY_MINUTES: tuple[int, ...] = (15, 60, 240)
# After exhausting short retries, fall back to full rolling window.
MAX_SHORT_RETRIES = len(RETRY_DELAY_MINUTES)


def spacing_seconds_for_count(
    count: int,
    *,
    window_hours: float = PRODUCTION_ROLLING_WINDOW_HOURS,
) -> float:
    """Even spacing across the rolling window (e.g. ~2.88 min for 1000 objects / 48h)."""
    if count <= 1:
        return 0.0
    return (window_hours * 3600) / count


def spread_next_check_schedule(
    object_ids: list[str],
    *,
    now: datetime,
    window_hours: float = PRODUCTION_ROLLING_WINDOW_HOURS,
) -> dict[str, datetime]:
    """Spread first next_check_at across ~window_hours without burst at t=0."""
    ids = sorted(object_ids)
    if not ids:
        return {}
    step = spacing_seconds_for_count(len(ids), window_hours=window_hours)
    return {
        oid: now + timedelta(seconds=round(i * step))
        for i, oid in enumerate(ids)
    }


def compute_retry_next_check_at(
    *,
    now: datetime,
    retry_count: int,
    fallback_tier: RefreshTier = RefreshTier.H48,
) -> datetime:
    """Bounded retry delays; after MAX_SHORT_RETRIES use full tier window (48H)."""
    if retry_count >= MAX_SHORT_RETRIES:
        hours = TIER_HOURS.get(fallback_tier, PRODUCTION_ROLLING_WINDOW_HOURS)
        return now + timedelta(hours=hours)
    idx = min(retry_count, len(RETRY_DELAY_MINUTES) - 1)
    return now + timedelta(minutes=RETRY_DELAY_MINUTES[idx])


def production_tier_for_source(source: SourceKind) -> RefreshTier:
    if source == SourceKind.FACEBOOK:
        return RefreshTier.H78
    return RefreshTier.H48


def tier_after_success(current_tier: RefreshTier, source: SourceKind = SourceKind.AIRBNB) -> RefreshTier:
    """FIRST_REFRESH → source production tier after first successful refresh."""
    if current_tier == RefreshTier.FIRST_REFRESH:
        return production_tier_for_source(source)
    return current_tier


def retry_fallback_tier(source: SourceKind) -> RefreshTier:
    return production_tier_for_source(source)
