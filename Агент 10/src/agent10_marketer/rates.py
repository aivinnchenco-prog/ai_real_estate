"""Rate and velocity helpers. Missing metrics stay None; never divide by zero."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent10_marketer.config import ScoringConfig
from agent10_marketer.models import AnalyticsMetrics, ContentFormat, Platform


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def age_hours(published_at: datetime | None, *, now: datetime | None = None) -> float | None:
    if published_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = (now - published_at).total_seconds() / 3600.0
    return max(delta, 0.0)


def pick_denominator(metrics: dict[str, float | None], priority: list[str]) -> float | None:
    for key in priority:
        value = metrics.get(key)
        if value is not None and value > 0:
            return float(value)
    return None


def compute_rates(
    *,
    reach: float | None,
    impressions: float | None,
    views: float | None,
    likes: float | None,
    comments: float | None,
    saves: float | None,
    shares: float | None,
    age_h: float | None,
    config: ScoringConfig,
) -> dict[str, float | None]:
    base = {
        "impressions": impressions,
        "reach": reach,
        "views": views,
    }
    denom = pick_denominator(base, config.rate_denominator_priority)
    engagement_num: float | None = None
    parts = [likes, comments, saves, shares]
    present = [p for p in parts if p is not None]
    if present:
        engagement_num = float(sum(present))

    min_age = max(float(config.min_age_hours), 1e-9)
    age_norm = None if age_h is None else max(float(age_h), min_age)

    reach_velocity = safe_div(reach, age_norm)
    view_velocity = safe_div(views, age_norm)

    return {
        "save_rate": safe_div(saves, denom),
        "comment_rate": safe_div(comments, denom),
        "share_rate": safe_div(shares, denom),
        "engagement_rate": safe_div(engagement_num, denom),
        "reach_velocity": reach_velocity,
        "view_velocity": view_velocity,
    }


def build_analytics(
    *,
    publication_id: str,
    object_id: str,
    platform: Platform,
    format: ContentFormat,
    published_at: datetime | None,
    raw: dict[str, Any] | None,
    config: ScoringConfig,
    source: str = "unknown",
    now: datetime | None = None,
) -> AnalyticsMetrics:
    """Build AnalyticsMetrics from raw provider dict. Absent keys → None (not 0)."""
    raw = raw or {}
    notes: list[str] = []

    def _num(key: str) -> float | None:
        if key not in raw or raw[key] is None:
            return None
        try:
            return float(raw[key])
        except (TypeError, ValueError):
            notes.append(f"invalid_numeric:{key}")
            return None

    age_h = age_hours(published_at, now=now)
    reach = _num("reach")
    impressions = _num("impressions")
    views = _num("views")
    likes = _num("likes")
    comments = _num("comments")
    saves = _num("saves")
    shares = _num("shares")

    derived = compute_rates(
        reach=reach,
        impressions=impressions,
        views=views,
        likes=likes,
        comments=comments,
        saves=saves,
        shares=shares,
        age_h=age_h,
        config=config,
    )

    core = [reach, impressions, views, likes, comments, saves, shares]
    if all(v is None for v in core):
        quality = "empty" if not raw else "unavailable"
    elif any(v is None for v in core):
        quality = "partial"
    else:
        quality = "full"

    return AnalyticsMetrics(
        publication_id=publication_id,
        object_id=object_id,
        platform=platform,
        format=format,
        published_at=published_at,
        age_hours=age_h,
        reach=reach,
        impressions=impressions,
        views=views,
        likes=likes,
        comments=comments,
        saves=saves,
        shares=shares,
        save_rate=derived["save_rate"],
        comment_rate=derived["comment_rate"],
        share_rate=derived["share_rate"],
        engagement_rate=derived["engagement_rate"],
        reach_velocity=derived["reach_velocity"],
        view_velocity=derived["view_velocity"],
        data_quality=quality,
        source=source,
        notes=notes,
    )
