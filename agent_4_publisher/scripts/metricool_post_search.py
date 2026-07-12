#!/usr/bin/env python3
"""Find Metricool scheduled posts by Notion object_id and platform."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from publish_pipeline import (
    get_prop,
    metricool_get_post,
    metricool_headers,
    metricool_timezone,
    metricool_url,
    network_for,
    notion_get_page,
    req,
)


def object_id_from_page(page: dict[str, Any], fields: dict[str, str]) -> str | None:
    value = get_prop(page, fields.get("object_id", "Объект ID"), "rich_text")
    return value.strip() if value else None


def post_text_matches_object_id(post: dict[str, Any], object_id: str) -> bool:
    oid = object_id.strip()
    if not oid:
        return False
    haystack = "\n".join(
        part
        for part in (
            post.get("text"),
            post.get("firstCommentText"),
        )
        if isinstance(part, str)
    ).lower()
    markers = (
        oid,
        f"#{oid}",
        f"№{oid}",
        f"no.{oid}",
        f"no {oid}",
    )
    return any(marker.lower() in haystack for marker in markers)


def instagram_kind_from_post(post: dict[str, Any]) -> str | None:
    post_type = str((post.get("instagramData") or {}).get("type") or "").upper()
    if post_type == "REEL":
        return "reel"
    if post_type == "POST":
        return "carousel"
    return None


def post_matches_platform(
    post: dict[str, Any],
    platform: str,
    *,
    post_kind: str | None = None,
) -> bool:
    network = network_for(platform)
    providers = post.get("providers")
    if not isinstance(providers, list):
        return False
    has_network = any(
        isinstance(provider, dict)
        and (
            provider.get("network") == network
            or network_for(str(provider.get("network") or "")) == network
        )
        for provider in providers
    )
    if not has_network:
        return False
    if network == "instagram" and post_kind in {"carousel", "reel"}:
        return instagram_kind_from_post(post) == post_kind
    return True


def provider_for_network(post: dict[str, Any], network: str) -> dict[str, Any] | None:
    for provider in post.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        provider_network = provider.get("network")
        if provider_network == network or network_for(str(provider_network or "")) == network:
            return provider
    return None


def _publication_sort_key(post: dict[str, Any]) -> str:
    pub = post.get("publicationDate") or {}
    date_time = pub.get("dateTime")
    if not isinstance(date_time, str):
        return ""
    return date_time


def _rank_post(post: dict[str, Any], network: str) -> tuple[int, str, int]:
    provider = provider_for_network(post, network) or {}
    status = str(provider.get("status") or "").upper()
    score = 0
    if status == "PUBLISHED":
        score += 100
    elif status == "PUBLISHING":
        score += 80
    elif status == "PENDING":
        score += 40
    if provider.get("publicUrl"):
        score += 50
    post_id = post.get("id")
    numeric_id = int(post_id) if str(post_id).isdigit() else 0
    return (score, _publication_sort_key(post), numeric_id)


def search_window_for_page(
    page: dict[str, Any],
    fields: dict[str, str],
    config: dict[str, Any],
) -> tuple[str, str]:
    mc = config.get("metricool", {})
    days_back = int(mc.get("post_search_days_back", 14))
    days_ahead = int(mc.get("post_search_days_ahead", 2))
    tz_name = metricool_timezone(config)
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)

    taken_at = get_prop(page, fields.get("agent6_taken_at", "agent6_taken_at"), "date")
    if taken_at:
        start = datetime.fromisoformat(taken_at).replace(tzinfo=tz) - timedelta(days=1)
    else:
        start = now - timedelta(days=days_back)
    end = now + timedelta(days=days_ahead)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)


def metricool_list_posts(
    config: dict[str, Any],
    *,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    result = req(
        "GET",
        metricool_url(
            "/v2/scheduler/posts",
            {
                "start": start,
                "end": end,
                "timezone": metricool_timezone(config),
                "extendedRange": "true",
            },
        ),
        metricool_headers(),
    )
    data = result.get("data") if isinstance(result, dict) else result
    return data if isinstance(data, list) else []


def find_metricool_post(
    page: dict[str, Any],
    platform: str,
    config: dict[str, Any],
    *,
    post_kind: str | None = None,
) -> dict[str, Any] | None:
    fields = config["notion"]["fields"]
    object_id = object_id_from_page(page, fields)
    if not object_id:
        return None

    network = network_for(platform)
    start, end = search_window_for_page(page, fields, config)
    posts = metricool_list_posts(config, start=start, end=end)

    candidates: list[dict[str, Any]] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        if not post_text_matches_object_id(post, object_id):
            continue
        if not post_matches_platform(post, platform, post_kind=post_kind):
            continue
        candidates.append(post)

    if not candidates:
        return None

    best = max(candidates, key=lambda post: _rank_post(post, network))
    post_id = best.get("id")
    if post_id is None:
        return None
    return {
        "post_id": str(post_id),
        "post": best,
        "object_id": object_id,
        "search_range": {"start": start, "end": end},
        "match_count": len(candidates),
    }


def resolve_metricool_post_id(
    page_id: str,
    platform: str,
    config: dict[str, Any],
    *,
    explicit_post_id: str | None = None,
    page: dict[str, Any] | None = None,
    post_kind: str | None = None,
) -> dict[str, Any]:
    page = page or notion_get_page(page_id)
    network = network_for(platform)
    result: dict[str, Any] = {
        "page_id": page_id,
        "platform": platform,
        "post_kind": post_kind,
        "source": None,
        "post_id": None,
    }

    if explicit_post_id:
        try:
            post = metricool_get_post(explicit_post_id)
            if post_matches_platform(post, platform, post_kind=post_kind):
                result.update(
                    {
                        "source": "explicit",
                        "post_id": str(explicit_post_id),
                        "post": post,
                    }
                )
                return result
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
        result["explicit_post_id_missing"] = explicit_post_id

    found = find_metricool_post(page, platform, config, post_kind=post_kind)
    if found:
        result.update(
            {
                "source": "object_id_search",
                "post_id": found["post_id"],
                "post": found["post"],
                "object_id": found["object_id"],
                "search_range": found["search_range"],
                "match_count": found["match_count"],
            }
        )
        return result

    fields = config["notion"]["fields"]
    pg_field = fields.get("metricool_post_id", "metricool_post_id")
    fallback_id = None
    prop = page.get("properties", {}).get(pg_field, {})
    items = prop.get("rich_text") or []
    fallback_id = "".join(item.get("plain_text", "") for item in items).strip()
    if fallback_id and fallback_id != explicit_post_id:
        try:
            post = metricool_get_post(fallback_id)
            if post_matches_platform(post, platform, post_kind=post_kind):
                provider = provider_for_network(post, network)
                if provider:
                    result.update(
                        {
                            "source": "notion_metricool_post_id",
                            "post_id": fallback_id,
                            "post": post,
                        }
                    )
                    return result
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise

    return result
