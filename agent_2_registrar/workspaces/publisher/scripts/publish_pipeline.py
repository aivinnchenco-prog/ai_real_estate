#!/usr/bin/env python3
"""
Agent 6 — Notion CRM → Publora publisher pipeline.

Usage:
  python3 publish_pipeline.py --page-id PAGE_ID --platform instagram
  python3 publish_pipeline.py --queue --platform tiktok
  python3 publish_pipeline.py --page-id PAGE_ID --platform youtube --dry-run
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from pathlib import Path
from typing import Any

NOTION_VERSION = "2022-06-28"
PUBLORA_BASE = "https://api.publora.com/api/v1"

VIDEO_FIELDS = {
    "vertical": "video_url_vertical",
    "seedance": "video_url_Seedance",
}

PLATFORM_ENV = {
    "instagram": "PUBLORA_PLATFORM_INSTAGRAM",
    "instagram_feed": "PUBLORA_PLATFORM_INSTAGRAM",
    "tiktok": "PUBLORA_PLATFORM_TIKTOK",
    "youtube": "PUBLORA_PLATFORM_YOUTUBE",
    "youtube_shorts": "PUBLORA_PLATFORM_YOUTUBE",
    "facebook": "PUBLORA_PLATFORM_FACEBOOK",
    "facebook_square": "PUBLORA_PLATFORM_FACEBOOK",
    "twitter": "PUBLORA_PLATFORM_TWITTER",
    "x": "PUBLORA_PLATFORM_TWITTER",
    "threads": "PUBLORA_PLATFORM_THREADS",
    "linkedin": "PUBLORA_PLATFORM_LINKEDIN",
}


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config() -> dict[str, Any]:
    config_path = package_root() / "config" / "publisher.json"
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)


def load_dotenv() -> None:
    root = package_root()
    env_files = [
        root / ".env",
        root / ".env.local",
        root.parents[1] / "_import" / "assistant-media" / ".env.real-estate",
    ]
    for env_path in env_files:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
        if os.environ.get("NOTION_API_KEY") and os.environ.get("PUBLORA_API_KEY"):
            break


def req(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        request.add_header(k, v)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {url}: {err_body}") from e


def notion_headers() -> dict[str, str]:
    key = os.environ["NOTION_API_KEY"]
    return {
        "Authorization": f"Bearer {key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def publora_headers() -> dict[str, str]:
    return {
        "x-publora-key": os.environ["PUBLORA_API_KEY"],
        "Content-Type": "application/json",
    }


def get_prop(page: dict[str, Any], name: str, ptype: str) -> Any:
    props = page.get("properties", {})
    prop = props.get(name, {})
    if prop.get("type") != ptype:
        return None
    if ptype == "url":
        return prop.get("url")
    if ptype == "status":
        st = prop.get("status") or {}
        return st.get("name")
    if ptype == "title":
        items = prop.get("title") or []
        return "".join(t.get("plain_text", "") for t in items)
    if ptype == "rich_text":
        items = prop.get("rich_text") or []
        return "".join(t.get("plain_text", "") for t in items)
    return None


def notion_get_page(page_id: str) -> dict[str, Any]:
    return req("GET", f"https://api.notion.com/v1/pages/{page_id}", notion_headers())


def notion_query_ready(database_id: str, status_ready: str, status_field: str) -> list[dict[str, Any]]:
    payload = json.dumps(
        {"filter": {"property": status_field, "status": {"equals": status_ready}}}
    ).encode("utf-8")
    result = req(
        "POST",
        f"https://api.notion.com/v1/databases/{database_id}/query",
        notion_headers(),
        payload,
    )
    return result.get("results", [])


def resolve_status(name: str, config: dict[str, Any]) -> str:
    """Env override > config > default."""
    env_map = {
        "ready": "NOTION_STATUS_READY",
        "taken": "NOTION_STATUS_TAKEN",
        "scheduled": "NOTION_STATUS_SCHEDULED",
        "failed": "NOTION_STATUS_FAILED",
    }
    env_key = env_map.get(name)
    if env_key and os.environ.get(env_key):
        return os.environ[env_key]
    return config["notion"]["statuses"][name]


def notion_update_status(page_id: str, status_field: str, status_name: str) -> None:
    payload = json.dumps(
        {"properties": {status_field: {"status": {"name": status_name}}}}
    ).encode("utf-8")
    req("PATCH", f"https://api.notion.com/v1/pages/{page_id}", notion_headers(), payload)


def notion_update_status_safe(
    page_id: str, status_field: str, status_name: str, fallback: str | None = None
) -> str:
    try:
        notion_update_status(page_id, status_field, status_name)
        return status_name
    except RuntimeError:
        if fallback and fallback != status_name:
            notion_update_status(page_id, status_field, fallback)
            return fallback
        raise


def notion_update_fields(page_id: str, fields: dict[str, Any]) -> None:
    payload = json.dumps({"properties": fields}).encode("utf-8")
    req("PATCH", f"https://api.notion.com/v1/pages/{page_id}", notion_headers(), payload)


def pick_video_url(page: dict[str, Any], fields: dict[str, str], platform: str, mapping: dict[str, str]) -> tuple[str, str]:
    fmt = mapping.get(platform, "seedance")
    field_key = VIDEO_FIELDS.get(fmt, "video_url_Seedance")
    field_name = fields.get(field_key, field_key)
    url = get_prop(page, field_name, "url")
    if not url and fmt == "seedance":
        field_key = "video_url_vertical"
        field_name = fields.get(field_key, field_key)
        url = get_prop(page, field_name, "url")
    if not url:
        raise ValueError(f"No {field_name} for platform {platform} (format: {fmt})")
    return url, field_name


def resolve_carousel_urls(gallery_url: str, max_images: int) -> list[str]:
    if not gallery_url or max_images <= 0:
        return []
    if re.search(r"\.(jpe?g|png|webp|gif)(\?.*)?$", gallery_url, re.I):
        return [gallery_url]

    request = urllib.request.Request(gallery_url, method="GET")
    with urllib.request.urlopen(request, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    base = gallery_url if gallery_url.endswith("/") else gallery_url.rsplit("/", 1)[0] + "/"
    urls: list[str] = []
    seen: set[str] = set()
    for src in re.findall(r"""<img[^>]+src=["']([^"']+)["']""", html, re.I):
        full = src if src.startswith("http") else urljoin(base, src)
        if full in seen:
            continue
        if not re.search(r"\.(jpe?g|png|webp|gif)(\?.*)?$", full, re.I):
            continue
        seen.add(full)
        urls.append(full)
        if len(urls) >= max_images:
            break
    return urls


def carousel_enabled_for(platform: str, config: dict[str, Any]) -> bool:
    carousel = config.get("carousel", {})
    if not carousel.get("enabled", True):
        return False
    platforms = carousel.get("platforms", [])
    return not platforms or platform in platforms


def upload_video_for_post(platform: str, carousel_urls: list[str], config: dict[str, Any]) -> bool:
    carousel = config.get("carousel", {})
    image_only = set(carousel.get("image_only_when_carousel", []))
    if carousel_urls and platform in image_only:
        return False
    return True


def build_caption(page: dict[str, Any], fields: dict[str, str], platform: str, config: dict[str, Any]) -> str:
    caption_map = config.get("notion", {}).get("caption_by_platform", {})
    caption_key = caption_map.get(platform, "caption")
    field_name = fields.get(caption_key, fields["caption"])

    text = get_prop(page, field_name, "rich_text")
    if text:
        return text.strip()

    title = get_prop(page, fields["title"], "title") or "Property"
    description = get_prop(page, fields.get("description", "Описание"), "rich_text")
    if description:
        return f"{title}\n\n{description[:500]}".strip()
    raise ValueError(f"Empty caption — fill {field_name} or Описание in Notion")


def download_file(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=300) as resp:
        dest.write_bytes(resp.read())


def media_content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def publora_upload_file(post_group_id: str, file_path: Path, media_type: str) -> None:
    content_type = media_content_type(file_path)
    upload_req = json.dumps(
        {
            "fileName": file_path.name,
            "contentType": content_type,
            "type": media_type,
            "postGroupId": post_group_id,
        }
    ).encode("utf-8")
    upload_meta = req(
        "POST",
        f"{PUBLORA_BASE}/get-upload-url",
        publora_headers(),
        upload_req,
    )
    upload_url = upload_meta.get("uploadUrl")
    if not upload_url:
        raise RuntimeError(f"No uploadUrl: {upload_meta}")

    file_bytes = file_path.read_bytes()
    put = urllib.request.Request(
        upload_url,
        data=file_bytes,
        method="PUT",
        headers={"Content-Type": content_type},
    )
    with urllib.request.urlopen(put, timeout=600):
        pass


def publora_upload_video(post_group_id: str, video_path: Path) -> None:
    publora_upload_file(post_group_id, video_path, "video")


def publora_create_draft(content: str, platform_id: str, platform_settings: dict[str, Any] | None) -> str:
    body: dict[str, Any] = {"content": content, "platforms": [platform_id]}
    if platform_settings:
        body["platformSettings"] = platform_settings
    result = req(
        "POST",
        f"{PUBLORA_BASE}/create-post",
        publora_headers(),
        json.dumps(body).encode("utf-8"),
    )
    post_group_id = result.get("postGroupId")
    if not post_group_id:
        raise RuntimeError(f"Publora create-post failed: {result}")
    return post_group_id


def publora_schedule(post_group_id: str, scheduled_time: str) -> None:
    payload = json.dumps({"status": "scheduled", "scheduledTime": scheduled_time}).encode("utf-8")
    req("PUT", f"{PUBLORA_BASE}/update-post/{post_group_id}", publora_headers(), payload)


def platform_id_for(platform: str) -> str:
    env_key = PLATFORM_ENV.get(platform)
    if not env_key:
        raise ValueError(f"Unknown platform: {platform}")
    pid = os.environ.get(env_key)
    if not pid:
        raise ValueError(f"Set {env_key} in .env (get from platform-connections)")
    return pid


def default_schedule_time(minutes_ahead: int = 30) -> str:
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def publish_one(
    page_id: str,
    platform: str,
    scheduled_time: str,
    dry_run: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    cfg_notion = config["notion"]
    fields = cfg_notion["fields"]
    statuses = cfg_notion["statuses"]
    mapping = config["video_format_by_platform"]

    page = notion_get_page(page_id)
    current_status = get_prop(page, fields["status"], "status")
    video_url, video_field = pick_video_url(page, fields, platform, mapping)
    caption = build_caption(page, fields, platform, config)

    photo_field = fields.get("photo", "Фото")
    gallery_url = get_prop(page, photo_field, "url")
    carousel_cfg = config.get("carousel", {})
    carousel_urls: list[str] = []
    if carousel_enabled_for(platform, config) and gallery_url:
        carousel_urls = resolve_carousel_urls(gallery_url, int(carousel_cfg.get("max_images", 10)))

    result = {
        "page_id": page_id,
        "platform": platform,
        "video_field": video_field,
        "video_url": video_url,
        "gallery_url": gallery_url,
        "carousel_images": len(carousel_urls),
        "carousel_urls_preview": carousel_urls[:3],
        "upload_video": upload_video_for_post(platform, carousel_urls, config),
        "caption_preview": caption[:120],
        "previous_status": current_status,
        "scheduled_time": scheduled_time,
    }

    if dry_run:
        result["dry_run"] = True
        try:
            result["platform_id"] = platform_id_for(platform)
        except ValueError as e:
            result["platform_id"] = f"(not set: {e})"
        return result

    platform_id = platform_id_for(platform)
    result["platform_id"] = platform_id

    status_taken = resolve_status("taken", config)
    status_scheduled = resolve_status("scheduled", config)
    status_failed = resolve_status("failed", config)

    applied_taken = notion_update_status_safe(
        page_id, fields["status"], status_taken, fallback="video_in_progress"
    )
    result["notion_status_taken"] = applied_taken

    try:
        ps = config.get("publora_platform_settings", {})
        platform_key = platform.split("_")[0]
        platform_settings = {platform_key: ps[platform_key]} if platform_key in ps else None

        post_group_id = publora_create_draft(caption, platform_id, platform_settings)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            if upload_video_for_post(platform, carousel_urls, config):
                video_path = tmp_path / "property.mp4"
                download_file(video_url, video_path)
                publora_upload_video(post_group_id, video_path)

            for idx, image_url in enumerate(carousel_urls):
                ext = Path(image_url.split("?")[0]).suffix or ".jpg"
                image_path = tmp_path / f"carousel_{idx + 1}{ext}"
                download_file(image_url, image_path)
                publora_upload_file(post_group_id, image_path, "image")

            if not upload_video_for_post(platform, carousel_urls, config) and not carousel_urls:
                raise ValueError(f"No carousel images from Фото for image-only platform {platform}")

        publora_schedule(post_group_id, scheduled_time)

        pg_field = fields.get("publora_post_group_id", "publora_post_group_id")
        notion_update_fields(
            page_id,
            {
                fields["status"]: {"status": {"name": status_scheduled}},
                pg_field: {"rich_text": [{"text": {"content": post_group_id}}]},
                fields.get("publish_error", "last_error"): {"rich_text": []},
            },
        )

        result["publora_post_group_id"] = post_group_id
        result["notion_status"] = status_scheduled
        return result

    except Exception as e:
        err_field = fields.get("publish_error", "last_error")
        err_count = page.get("properties", {}).get("error_count", {}).get("number") or 0
        notion_update_fields(
            page_id,
            {
                fields["status"]: {"status": {"name": status_failed}},
                err_field: {"rich_text": [{"text": {"content": str(e)[:2000]}}]},
                "error_count": {"number": err_count + 1},
            },
        )
        raise


def main() -> int:
    load_dotenv()
    config = load_config()

    parser = argparse.ArgumentParser(description="Notion CRM → Publora publisher")
    parser.add_argument("--page-id", help="Notion page ID")
    parser.add_argument("--platform", help="instagram|tiktok|youtube|all|...")
    parser.add_argument("--schedule", help="ISO8601 UTC scheduled time")
    parser.add_argument("--queue", action="store_true", help="Process all ready records")
    parser.add_argument("--dry-run", action="store_true", help="Validate without publishing")
    args = parser.parse_args()

    scheduled = args.schedule or default_schedule_time()

    if args.queue:
        database_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("NOTION_DB_ID")
        if not database_id:
            print("Set NOTION_DATABASE_ID or NOTION_DB_ID in .env", file=sys.stderr)
            return 1
        status_field = config["notion"]["fields"]["status"]
        status_ready = config["notion"]["statuses"]["ready"]
        pages = notion_query_ready(database_id, status_ready, status_field)
        if not pages:
            print("Queue empty.")
            return 0
        for page in pages:
            pid = page["id"]
            print(f"\n--- Processing {pid} ---")
            try:
                out = publish_one(pid, args.platform, scheduled, args.dry_run, config)
                print(json.dumps(out, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"ERROR {pid}: {e}", file=sys.stderr)
        return 0

    if not args.page_id and not args.queue:
        parser.error("--page-id required unless --queue")

    platforms = []
    if args.platform == "all":
        platforms = ["instagram", "tiktok", "youtube"]
    elif args.platform:
        platforms = [args.platform]
    else:
        parser.error("--platform required")

    if args.page_id:
        for platform in platforms:
            out = publish_one(args.page_id, platform, scheduled, args.dry_run, config)
            print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if not args.page_id:
        parser.error("--page-id required unless --queue")

    return 0


if __name__ == "__main__":
    sys.exit(main())
