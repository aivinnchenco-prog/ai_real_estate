#!/usr/bin/env python3
"""PostMyPost REST API client for Agent 6 social publishing."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

POSTMYPOST_BASE_DEFAULT = "https://api.postmypost.io/v4.1"

PUBLICATION_TYPE_POST = 1
PUBLICATION_TYPE_REELS = 4
PUBLICATION_STATUS_PENDING = 5
PUBLICATION_STATUS_PUBLISHED = 1
POST_STATUS_PUBLISHED = 1

KNOWN_CHANNEL_IDS: dict[int, str] = {
    16: "youtube",
}

PLATFORM_CHANNEL: dict[str, str] = {
    "instagram": "instagram",
    "instagram_feed": "instagram",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "youtube_shorts": "youtube",
    "facebook": "facebook",
    "facebook_square": "facebook",
    "twitter": "twitter",
    "x": "twitter",
    "linkedin": "linkedin",
    "threads": "threads",
}


def postmypost_cfg(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config is None:
        from publish_pipeline import load_config

        config = load_config()
    return config.get("postmypost") or {}


def postmypost_enabled(config: dict[str, Any] | None = None) -> bool:
    return bool(postmypost_cfg(config).get("enabled"))


def postmypost_timezone(config: dict[str, Any]) -> str:
    return (
        os.environ.get("POSTMYPOST_TIMEZONE")
        or postmypost_cfg(config).get("timezone")
        or "Asia/Bangkok"
    )


def postmypost_credentials() -> dict[str, str]:
    token = os.environ.get("POSTMYPOST_API_TOKEN") or os.environ.get("POSTMYPOST_TOKEN")
    if not token:
        raise ValueError("Set POSTMYPOST_API_TOKEN in .env")
    return {"token": token}


def postmypost_base_url(config: dict[str, Any]) -> str:
    return str(postmypost_cfg(config).get("base_url") or POSTMYPOST_BASE_DEFAULT).rstrip("/")


def postmypost_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> Any:
    cfg = config or {}
    url = f"{postmypost_base_url(cfg)}{path}"
    creds = postmypost_credentials()
    headers = {
        "Authorization": f"Bearer {creds['token']}",
        "Accept": "application/json",
        "User-Agent": "real-estate-agent6-publisher/1.0",
    }
    data: bytes | None = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PostMyPost HTTP {exc.code} {path}: {err_body}") from exc


def format_post_at(scheduled_time: str, tz_name: str) -> str:
    if scheduled_time.endswith("Z"):
        dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(scheduled_time)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ZoneInfo(tz_name))
    return local.isoformat(timespec="seconds")


def list_projects(config: dict[str, Any]) -> list[dict[str, Any]]:
    result = postmypost_request("GET", "/projects", config=config)
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        return result["data"]
    return []


def list_accounts(project_id: int, config: dict[str, Any]) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"project_id": project_id})
    result = postmypost_request("GET", f"/accounts?{query}", config=config)
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        return result["data"]
    return []


_channel_code_cache: dict[int, str] | None = None


def channel_code_by_id(config: dict[str, Any]) -> dict[int, str]:
    global _channel_code_cache
    if _channel_code_cache is not None:
        return _channel_code_cache
    result = postmypost_request("GET", "/channels", config=config)
    mapping: dict[int, str] = dict(KNOWN_CHANNEL_IDS)
    if isinstance(result, dict):
        for item in result.get("data") or []:
            if isinstance(item, dict) and item.get("id") is not None:
                mapping[int(item["id"])] = str(item.get("code") or "")
    _channel_code_cache = mapping
    return mapping


def resolve_account_ids(platform: str, config: dict[str, Any]) -> list[int]:
    from publish_pipeline import network_for

    network = network_for(platform)
    pmap = postmypost_cfg(config).get("platform_accounts") or {}
    explicit = pmap.get(platform) or pmap.get(network)
    if explicit:
        return [int(x) for x in explicit]

    project_id = int(postmypost_cfg(config).get("project_id") or 0)
    if not project_id:
        raise ValueError("postmypost.project_id is required in publisher.json")

    wanted = PLATFORM_CHANNEL.get(platform) or PLATFORM_CHANNEL.get(network) or network
    codes = channel_code_by_id(config)
    account_ids: list[int] = []
    for account in list_accounts(project_id, config):
        channel_id = account.get("chanel_id") or account.get("channel_id")
        code = codes.get(int(channel_id or 0), "")
        if code == wanted and int(account.get("connection_status") or 0) == 1:
            account_ids.append(int(account["id"]))
    if not account_ids:
        raise ValueError(
            f"No connected PostMyPost account for platform={platform!r} "
            f"(channel={wanted!r}, project_id={project_id})"
        )
    return account_ids


_file_id_cache: dict[str, int] = {}
_last_api_call_at: float = 0.0


def _rate_limit_pause(config: dict[str, Any]) -> None:
    global _last_api_call_at
    min_interval = float(postmypost_cfg(config).get("api_min_interval_seconds", 6.5))
    now = time.time()
    wait = min_interval - (now - _last_api_call_at)
    if wait > 0:
        time.sleep(wait)
    _last_api_call_at = time.time()


def resolve_rubric_id(object_id: str, config: dict[str, Any]) -> int | None:
    rubrics = postmypost_cfg(config).get("rubric_by_prefix") or {}
    for prefix, rubric_id in rubrics.items():
        if object_id.startswith(prefix) and rubric_id:
            return int(rubric_id)
    default = postmypost_cfg(config).get("default_rubric_id")
    return int(default) if default else None


def upload_file_by_url(project_id: int, media_url: str, config: dict[str, Any]) -> int:
    cached = _file_id_cache.get(media_url)
    if cached is not None:
        return cached

    _rate_limit_pause(config)
    init = postmypost_request(
        "POST",
        "/upload/init",
        {"project_id": project_id, "url": media_url},
        config=config,
    )
    upload_id = init.get("id")
    if upload_id is None:
        raise RuntimeError(f"PostMyPost upload/init failed: {init}")

    poll_seconds = float(postmypost_cfg(config).get("upload_poll_seconds", 2))
    poll_attempts = int(postmypost_cfg(config).get("upload_poll_attempts", 45))
    for _ in range(poll_attempts):
        status = postmypost_request(
            "GET",
            f"/upload/status?id={upload_id}",
            config=config,
        )
        state = int(status.get("status") or 0)
        if state == 1 and status.get("file_id") is not None:
            file_id = int(status["file_id"])
            _file_id_cache[media_url] = file_id
            return file_id
        if state == 2:
            raise RuntimeError(f"PostMyPost upload failed for {media_url}: {status}")
        time.sleep(poll_seconds)
    raise RuntimeError(f"PostMyPost upload timeout for {media_url} (id={upload_id})")


def postmypost_planner_url(publication_id: str | int, config: dict[str, Any]) -> str:
    template = postmypost_cfg(config).get("planner_url_template")
    if template:
        return str(template).format(publication_id=publication_id)
    return f"https://app.postmypost.io/publications/{publication_id}"


def extract_publication_url(
    response: dict[str, Any],
    platform: str,
    config: dict[str, Any] | None = None,
) -> str | None:
    """Live social permalink from posts[].url matched by platform account_id."""
    from postmypost_social_url import is_valid_social_post_url
    from publish_pipeline import network_for

    if config is None:
        config = postmypost_cfg(None)
        if not config.get("project_id"):
            from publish_pipeline import load_config

            config = load_config()

    if int(response.get("publication_status") or 0) != PUBLICATION_STATUS_PUBLISHED:
        return None

    network = network_for(platform)
    try:
        wanted_accounts = {int(x) for x in resolve_account_ids(platform, config)}
    except ValueError:
        return None

    for post in response.get("posts") or []:
        if not isinstance(post, dict):
            continue
        account_id = post.get("account_id")
        if account_id is None or int(account_id) not in wanted_accounts:
            continue
        post_status = post.get("post_status")
        if post_status is not None and int(post_status) != POST_STATUS_PUBLISHED:
            continue
        url = post.get("url")
        if isinstance(url, str) and url.startswith("http") and is_valid_social_post_url(url, network):
            return url
    return None


def postmypost_schedule_post(
    platform: str,
    caption: str,
    scheduled_time: str,
    video_url: str | None,
    carousel_urls: list[str],
    upload_video: bool,
    config: dict[str, Any],
    *,
    first_comment: str = "",
    title: str = "",
    object_id: str = "",
) -> dict[str, Any]:
    from publish_pipeline import network_for

    project_id = int(postmypost_cfg(config).get("project_id") or 0)
    if not project_id:
        raise ValueError("postmypost.project_id is required in publisher.json")

    account_ids = resolve_account_ids(platform, config)
    file_ids: list[int] = []
    if upload_video and video_url:
        file_ids.append(upload_file_by_url(project_id, video_url, config))
    for image_url in carousel_urls:
        file_ids.append(upload_file_by_url(project_id, image_url, config))
    if not file_ids:
        raise ValueError("No media to publish — need video or carousel images")

    publication_type = PUBLICATION_TYPE_REELS if upload_video else PUBLICATION_TYPE_POST
    detail: dict[str, Any] = {
        "publication_type": publication_type,
        "content": caption,
        "file_ids": file_ids,
    }
    if first_comment:
        detail["comment"] = first_comment
    if title:
        detail["title"] = title

    network = network_for(platform)
    platform_settings = (postmypost_cfg(config).get("platform_settings") or {}).get(network) or {}
    detail.update(platform_settings)

    if network == "instagram" and upload_video:
        detail.setdefault("instagram_share_to_feed", True)

    body = {
        "project_id": project_id,
        "post_at": format_post_at(scheduled_time, postmypost_timezone(config)),
        "account_ids": account_ids,
        "publication_status": int(
            postmypost_cfg(config).get("publication_status", PUBLICATION_STATUS_PENDING)
        ),
        "details": [detail],
    }
    rubric_id = resolve_rubric_id(object_id, config) if object_id else None
    if rubric_id:
        body["rubric_id"] = rubric_id

    _rate_limit_pause(config)
    result = postmypost_request("POST", "/publications", body, config=config)
    publication_id = result.get("id") if isinstance(result, dict) else None
    if publication_id is None:
        raise RuntimeError(f"PostMyPost schedule failed: {result}")
    return {
        "id": str(publication_id),
        "uuid": None,
        "response": result,
        "published_url": extract_publication_url(
            result if isinstance(result, dict) else {}, platform, config
        ),
    }


def postmypost_get_publication(publication_id: str, config: dict[str, Any]) -> dict[str, Any]:
    return postmypost_request("GET", f"/publications/{publication_id}", config=config)
