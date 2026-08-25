"""Production rolling scheduler policy (Airbnb success cadence, FIRST_REFRESH, bounded retries)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from .models import RefreshTier, SourceKind, TIER_HOURS

# After short retry ladder is exhausted, Airbnb errors fall back to 48h (not success cadence).
AIRBNB_ERROR_RETRY_FALLBACK_HOURS = 48
FACEBOOK_ROLLING_WINDOW_HOURS = 78
TARGET_OBJECTS_PER_HOUR = 21
MAX_SIMULTANEOUS_OBJECT_WORKERS = 1
FACEBOOK_MAX_SIMULTANEOUS_WORKERS = 1

# Minutes between retry attempts after ERROR (not full success interval).
RETRY_DELAY_MINUTES: tuple[int, ...] = (15, 60, 240)
# After exhausting short retries, fall back to error retry window (48h for Airbnb).
MAX_SHORT_RETRIES = len(RETRY_DELAY_MINUTES)


def airbnb_check_interval_hours() -> int:
    """Normal SUCCESS cadence for established Airbnb objects (env: AIRBNB_CHECK_INTERVAL_HOURS)."""
    raw = os.environ.get("AIRBNB_CHECK_INTERVAL_HOURS", "").strip()
    if raw:
        return max(1, int(raw))
    return 96


def production_rolling_window_hours() -> float:
    """Rolling spread/simulation window for Airbnb (matches success interval)."""
    return float(airbnb_check_interval_hours())


# Back-compat alias for imports; prefer production_rolling_window_hours().
PRODUCTION_ROLLING_WINDOW_HOURS = 96


def success_interval_hours(tier: RefreshTier) -> int:
    """Hours until next scheduled check after SUCCESS for tier."""
    if tier == RefreshTier.H48:
        return airbnb_check_interval_hours()
    return TIER_HOURS[tier]


def spacing_seconds_for_count(
    count: int,
    *,
    window_hours: float | None = None,
) -> float:
    """Even spacing across the rolling window."""
    if count <= 1:
        return 0.0
    hours = window_hours if window_hours is not None else production_rolling_window_hours()
    return (hours * 3600) / count


def spread_next_check_schedule(
    object_ids: list[str],
    *,
    now: datetime,
    window_hours: float | None = None,
) -> dict[str, datetime]:
    """Spread first next_check_at across rolling window without burst at t=0."""
    ids = sorted(object_ids)
    if not ids:
        return {}
    hours = window_hours if window_hours is not None else production_rolling_window_hours()
    step = spacing_seconds_for_count(len(ids), window_hours=hours)
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
    """Bounded retry delays; after MAX_SHORT_RETRIES use error fallback (48h Airbnb, 78h Facebook)."""
    if retry_count >= MAX_SHORT_RETRIES:
        if fallback_tier == RefreshTier.H48:
            hours = AIRBNB_ERROR_RETRY_FALLBACK_HOURS
        elif fallback_tier == RefreshTier.H78:
            hours = TIER_HOURS[RefreshTier.H78]
        else:
            hours = TIER_HOURS.get(fallback_tier, AIRBNB_ERROR_RETRY_FALLBACK_HOURS)
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
