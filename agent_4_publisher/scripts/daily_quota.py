#!/usr/bin/env python3
"""Дневные лимиты публикаций в Metricool (per network)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def _limits(config: dict[str, Any]) -> dict[str, Any]:
    return (config.get("agent6") or {}).get("per_network_daily_limit") or {}


def max_posts_per_day(config: dict[str, Any], platform: str) -> int:
    limits = _limits(config)
    network = _network_for(platform)
    per_net = limits.get("video_only_networks") or {}
    if network in per_net and "max_posts_per_day" in per_net[network]:
        return int(per_net[network]["max_posts_per_day"])
    return int(limits.get("max_posts_per_day", 4))


def carousel_cap(config: dict[str, Any], platform: str) -> int | None:
    limits = _limits(config)
    network = _network_for(platform)
    per_net = limits.get("video_only_networks") or {}
    if network in per_net:
        cap = per_net[network].get("carousel_max")
        if cap is not None:
            return int(cap)
    cap = limits.get("carousel_max")
    return int(cap) if cap is not None else None


def video_cap(config: dict[str, Any], platform: str) -> int | None:
    limits = _limits(config)
    network = _network_for(platform)
    per_net = limits.get("video_only_networks") or {}
    if network in per_net:
        cap = per_net[network].get("video_max")
        if cap is not None:
            return int(cap)
    cap = limits.get("video_max")
    return int(cap) if cap is not None else None


def _network_for(platform: str) -> str:
    from publish_pipeline import network_for

    return network_for(platform)


def _day_window(config: dict[str, Any], day_offset: int = 0) -> tuple[str, str, ZoneInfo]:
    """Окно суток по календарю бренда: 0 = сегодня, 1 = завтра, ..."""
    from publish_pipeline import metricool_timezone

    tz = ZoneInfo(metricool_timezone(config))
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
    end = start + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt), tz


def _post_is_video(post: dict[str, Any], network: str) -> bool:
    from metricool_post_search import instagram_kind_from_post

    if network == "instagram":
        kind = instagram_kind_from_post(post)
        if kind == "reel":
            return True
        if kind == "carousel":
            return False
    media = post.get("media") or post.get("mediaUrls") or []
    if isinstance(media, list):
        for item in media:
            url = item if isinstance(item, str) else (item or {}).get("url", "")
            if isinstance(url, str) and url.lower().endswith((".mp4", ".mov", ".webm")):
                return True
    return bool(post.get("videoUrl") or post.get("video"))


def _provider_active(provider: dict[str, Any]) -> bool:
    status = str(provider.get("status") or "").upper()
    return status not in {"DELETED", "CANCELLED", "FAILED", "ERROR"}


def count_today_posts(
    network: str, config: dict[str, Any], day_offset: int = 0
) -> dict[str, int]:
    """Сколько постов уже в Metricool на сутки (по календарю бренда)."""
    from metricool_post_search import metricool_list_posts, provider_for_network

    start, end, _ = _day_window(config, day_offset)
    try:
        posts = metricool_list_posts(config, start=start, end=end)
    except Exception:
        return {"total": 0, "carousel": 0, "video": 0}

    total = carousel = video = 0
    for post in posts:
        if not isinstance(post, dict):
            continue
        provider = provider_for_network(post, network)
        if not provider or not _provider_active(provider):
            continue
        total += 1
        if _post_is_video(post, network):
            video += 1
        else:
            carousel += 1
    return {"total": total, "carousel": carousel, "video": video}


def check_publish_quota(
    platform: str,
    config: dict[str, Any],
    *,
    upload_video: bool,
    mode: str | None = None,
    force: bool = False,
    day_offset: int = 0,
) -> str | None:
    """Вернуть причину «квота занята» или None, если слот доступен.

    day_offset: 0 = сегодня, 1 = завтра, ... (по календарю бренда Metricool).
    """
    agent6 = config.get("agent6") or {}
    if force and not agent6.get("count_force_as_quota", False):
        return None

    network = _network_for(platform)
    is_video = upload_video or mode == "video"
    counts = count_today_posts(network, config, day_offset)
    cap_total = max_posts_per_day(config, platform)
    day = "сегодня" if day_offset == 0 else f"день +{day_offset}"

    if counts["total"] >= cap_total:
        return f"daily_quota: {network} {counts['total']}/{cap_total} постов на {day}"

    cap_c = carousel_cap(config, platform)
    if not is_video and cap_c is not None and counts["carousel"] >= cap_c:
        return f"daily_quota: {network} каруселей {counts['carousel']}/{cap_c} на {day}"

    cap_v = video_cap(config, platform)
    if is_video and cap_v is not None and counts["video"] >= cap_v:
        return f"daily_quota: {network} видео {counts['video']}/{cap_v} на {day}"

    return None


def find_free_day(
    platform: str,
    config: dict[str, Any],
    *,
    upload_video: bool,
    mode: str | None = None,
    force: bool = False,
) -> tuple[int | None, str | None]:
    """Первый день со свободным слотом квоты: (day_offset, причина_на_сегодня).

    Квота на сегодня занята — пост не пропускается, а планируется на первый
    свободный день (завтра, послезавтра, ...). None — нет слота в пределах
    agent6.quota_lookahead_days (по умолчанию 7).
    """
    lookahead = int((config.get("agent6") or {}).get("quota_lookahead_days", 7))
    today_reason: str | None = None
    for offset in range(0, lookahead + 1):
        reason = check_publish_quota(
            platform, config,
            upload_video=upload_video, mode=mode, force=force,
            day_offset=offset,
        )
        if offset == 0:
            today_reason = reason
        if reason is None:
            return offset, today_reason
    return None, today_reason
