#!/usr/bin/env python3
"""
Agent 6 — Notion CRM → Metricool publisher pipeline.

Usage:
  python3 publish_pipeline.py --page-id PAGE_ID --platform instagram
  python3 publish_pipeline.py --queue --platform tiktok
  python3 publish_pipeline.py --page-id PAGE_ID --platform instagram --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from image_selection import sanitize_telegram_caption, select_diverse_images
from metricool_seo import build_metricool_caption_bundle
import notion_fields as nfc

NOTION_VERSION = "2022-06-28"
METRICOOL_BASE = "https://app.metricool.com/api"

VIDEO_FIELDS = {
    "vertical": nfc.VIDEO_URL_VERTICAL,
    "seedance": nfc.VIDEO_URL_SEEDANCE,
}

PLATFORM_NETWORK = {
    "instagram": "instagram",
    "instagram_feed": "instagram",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "youtube_shorts": "youtube",
    "facebook": "facebook",
    "facebook_square": "facebook",
    "twitter": "twitter",
    "x": "twitter",
    "threads": "threads",
    "linkedin": "linkedin",
}

_DEFAULT_PUBLISH_PLATFORMS = [
    "instagram",
    "tiktok",
    "x",
    "linkedin",
    "facebook",
    "youtube",
    "threads",
]

NETWORK_URL_HINTS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "twitter": ("x.com", "twitter.com"),
    "linkedin": ("linkedin.com",),
    "facebook": ("facebook.com", "fb.watch", "fb.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "threads": ("threads.net", "threads.com"),
}

URL_KEY_HINTS = ("url", "link", "permalink", "href")
SKIP_URL_PARENT_KEYS = frozenset({"location", "media", "mediaAltText"})

FACEBOOK_POST_MARKERS = (
    "/posts/",
    "story_fbid",
    "permalink.php",
    "photo.php",
    "/reel/",
    "/videos/",
    "fb.watch/",
)
FACEBOOK_PLACE_ONLY = re.compile(
    r"^https?://(?:www\.|m\.)?facebook\.com/\d+/?(?:\?.*)?$",
    re.IGNORECASE,
)


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config() -> dict[str, Any]:
    config_path = package_root() / "config" / "publisher.json"
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)


def metricool_enabled(config: dict[str, Any] | None = None) -> bool:
    """Metricool-ветка включена? По умолчанию true для обратной совместимости."""
    cfg = load_config() if config is None else config
    return bool(cfg.get("metricool", {}).get("enabled", True))


def phone_publisher_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = load_config() if config is None else config
    return bool(cfg.get("phone_publisher", {}).get("enabled", False))


def phone_publisher_root(config: dict[str, Any] | None = None) -> Path:
    """Корень Publisher social: в монорепе или legacy рядом в «Агенты»."""
    cfg = config or load_config()
    rel = cfg.get("phone_publisher", {}).get("project_path", "agent_4_publisher_social")
    path = Path(rel).expanduser()
    if path.is_absolute():
        return path.resolve()
    monorepo = package_root().parent  # Real Estate Agent
    inside = (monorepo / rel).resolve()
    if inside.exists():
        return inside
    # Legacy: …/Агенты/Publisher social
    legacy = (monorepo.parent / rel).resolve()
    if legacy.exists():
        return legacy
    legacy_default = (monorepo.parent / "Publisher social").resolve()
    if legacy_default.exists():
        return legacy_default
    return inside


def _publish_platforms_from_config() -> list[str]:
    try:
        platforms = load_config().get("publish_platforms")
    except (OSError, json.JSONDecodeError):
        platforms = None
    return list(platforms) if platforms else list(_DEFAULT_PUBLISH_PLATFORMS)


# Полный список платформ для публикации/синка URL; правится в config/publisher.json
ALL_PUBLISH_PLATFORMS = _publish_platforms_from_config()


def load_dotenv() -> None:
    root = package_root()
    env_files = [
        root / ".env",
        root / ".env.local",
        root.parent / "agent_2_registrar" / "_import" / "assistant-media" / ".env.real-estate",
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
        if os.environ.get("NOTION_API_KEY") and os.environ.get("METRICOOL_USER_TOKEN"):
            break


def run_schema_check(skip: bool) -> None:
    """Валидация живой схемы Notion против schema/notion_schema.json (общий контракт репо)."""
    if skip or os.environ.get("SKIP_SCHEMA_CHECK") == "1":
        print("[schema] проверка схемы пропущена (--skip-schema-check)")
        return
    for parent in Path(__file__).resolve().parents:
        validator = parent / "schema" / "validate_schema.py"
        if validator.exists():
            proc = subprocess.run([sys.executable, str(validator)])
            if proc.returncode != 0:
                print(
                    "[schema] Схема Notion не совпадает с контрактом. "
                    "Исправь таблицу/контракт или запусти с --skip-schema-check.",
                    file=sys.stderr,
                )
                sys.exit(2)
            return
    print("[schema] validate_schema.py не найден — проверка схемы пропущена", file=sys.stderr)


def metricool_credentials() -> dict[str, str]:
    token = os.environ.get("METRICOOL_USER_TOKEN") or os.environ.get("METRICOOL_API_TOKEN")
    user_id = os.environ.get("METRICOOL_USER_ID")
    blog_id = os.environ.get("METRICOOL_BLOG_ID")
    if not token or not user_id or not blog_id:
        raise ValueError("Set METRICOOL_USER_TOKEN, METRICOOL_USER_ID, METRICOOL_BLOG_ID in .env")
    return {"token": token, "user_id": user_id, "blog_id": blog_id}


def metricool_timezone(config: dict[str, Any]) -> str:
    return (
        os.environ.get("METRICOOL_TIMEZONE")
        or config.get("metricool", {}).get("timezone")
        or "Asia/Bangkok"
    )


def metricool_url(path: str, extra: dict[str, str] | None = None) -> str:
    creds = metricool_credentials()
    params = {"userId": creds["user_id"], "blogId": creds["blog_id"]}
    if extra:
        params.update(extra)
    return f"{METRICOOL_BASE}{path}?{urllib.parse.urlencode(params)}"


def metricool_headers() -> dict[str, str]:
    creds = metricool_credentials()
    return {
        "X-Mc-Auth": creds["token"],
        "Content-Type": "application/json",
    }


def req(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> Any:
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("User-Agent", "real-estate-agent6-publisher/1.0")
    for k, v in (headers or {}).items():
        request.add_header(k, v)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return {}
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body.strip()
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
    if ptype == "checkbox":
        return bool(prop.get("checkbox"))
    if ptype == "date":
        date = prop.get("date") or {}
        return date.get("start")
    if ptype == "number":
        return prop.get("number")
    if ptype == "select":
        sel = prop.get("select") or {}
        return sel.get("name")
    if ptype == "place":
        place = prop.get("place")
        return place if isinstance(place, dict) else None
    return None


def is_agent6_locked(page: dict[str, Any], fields: dict[str, str]) -> bool:
    lock_field = fields.get("agent6_locked")
    if not lock_field:
        return False
    return bool(get_prop(page, lock_field, "checkbox"))


def publish_skip_reason(
    page: dict[str, Any],
    fields: dict[str, str],
    platform: str,
    config: dict[str, Any],
    *,
    upload_video: bool,
    mode: str | None,
    bypass_lock: bool,
    force: bool,
) -> str | None:
    """Причина пропуска слота или None, если публиковать можно.

    force (--force CLI) обходит дневную квоту, не дубли уже запланированных слотов.
    bypass_lock — продолжение одного запуска после agent6_locked или добивка слотов.
    """
    if platform_slot_published(
        page, fields, platform, config, upload_video=upload_video, mode=mode,
    ):
        url_field = published_url_field(
            platform, config, upload_video=upload_video, mode=mode,
        )
        return f"already published ({url_field})"
    if is_agent6_locked(page, fields) and not bypass_lock and not force:
        return "agent6_locked"
    return None


def platform_slot_published(
    page: dict[str, Any],
    fields: dict[str, str],
    platform: str,
    config: dict[str, Any],
    *,
    upload_video: bool = False,
    mode: str | None = None,
) -> bool:
    """Уже запланирован/опубликован этот слот (IG carousel vs reel — отдельно)."""
    url_field = published_url_field(
        platform, config, upload_video=upload_video, mode=mode,
    )
    return bool(url_field and get_prop(page, url_field, "url"))


def utc_today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def notion_checkbox_property(checked: bool) -> dict[str, Any]:
    return {"checkbox": checked}


def notion_date_property(iso_date: str) -> dict[str, Any]:
    return {"date": {"start": iso_date}}


def notion_datetime_property(scheduled_time: str, tz_name: str) -> dict[str, Any]:
    """Notion date+time (всегда с часами/минутами) для календарного вида.

    Пишем ISO с явным offset (напр. 2026-07-25T15:30:00+07:00), чтобы UI
    Notion показывал время, а не только дату.
    """
    if scheduled_time.endswith("Z"):
        dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(scheduled_time)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ZoneInfo(tz_name))
    return {"date": {"start": local.isoformat(timespec="seconds")}}


def notion_get_page(page_id: str) -> dict[str, Any]:
    return req("GET", f"https://api.notion.com/v1/pages/{page_id}", notion_headers())


def notion_query_ready(
    database_id: str,
    status_ready: str,
    status_field: str,
    *,
    locked_field: str | None = None,
) -> list[dict[str, Any]]:
    if locked_field:
        filter_body: dict[str, Any] = {
            "and": [
                {"property": status_field, "status": {"equals": status_ready}},
                {"property": locked_field, "checkbox": {"equals": False}},
            ]
        }
    else:
        filter_body = {"property": status_field, "status": {"equals": status_ready}}
    payload = json.dumps({"filter": filter_body}).encode("utf-8")
    result = req(
        "POST",
        f"https://api.notion.com/v1/databases/{database_id}/query",
        notion_headers(),
        payload,
    )
    return result.get("results", [])


def resolve_status(name: str, config: dict[str, Any]) -> str:
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


def network_for(platform: str) -> str:
    network = PLATFORM_NETWORK.get(platform)
    if not network:
        raise ValueError(f"Unknown platform: {platform}")
    return network


def pick_video_url(page: dict[str, Any], fields: dict[str, str], platform: str, mapping: dict[str, str]) -> tuple[str, str]:
    fmt = mapping.get(platform, "seedance")
    field_key = VIDEO_FIELDS.get(fmt, nfc.VIDEO_URL_SEEDANCE)
    field_name = fields.get(field_key, field_key)
    url = get_prop(page, field_name, "url")
    if not url and fmt == "seedance":
        field_key = nfc.VIDEO_URL_VERTICAL
        field_name = fields.get(field_key, field_key)
        url = get_prop(page, field_name, "url")
    if not url:
        raise ValueError(f"No {field_name} for platform {platform} (format: {fmt})")
    return url, field_name


def parse_gallery_image_urls(gallery_url: str) -> list[str]:
    if not gallery_url:
        return []
    if re.search(r"\.(jpe?g|png|webp|gif)(\?.*)?$", gallery_url, re.I):
        return [gallery_url]

    request = urllib.request.Request(gallery_url, method="GET")
    request.add_header("User-Agent", "real-estate-agent6-publisher/1.0")
    with urllib.request.urlopen(request, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    base = gallery_url if gallery_url.endswith("/") else gallery_url.rsplit("/", 1)[0] + "/"
    urls: list[str] = []
    seen: set[str] = set()
    for src in re.findall(r"""<img[^>]+src=["']([^"']+)["']""", html, re.I):
        full = src if src.startswith("http") else urllib.parse.urljoin(base, src)
        if full in seen:
            continue
        if not re.search(r"\.(jpe?g|png|webp|gif)(\?.*)?$", full, re.I):
            continue
        seen.add(full)
        urls.append(full)
    return urls


def hook_cover_url(gallery_url: str) -> str | None:
    """Хук-обложка от Агента 3: {id}/hook_cover.jpg — вне папки photos/,
    чтобы клиент, получивший от Агента 6 ссылку на галерею, не видел цену
    на хуке (легаси-путь photos/000_hook_cover.jpg тоже проверяем)."""
    if not gallery_url or "/photos/" not in gallery_url:
        return None
    base = gallery_url.rsplit("/photos/", 1)[0]
    for candidate in (f"{base}/hook_cover.jpg", f"{base}/photos/000_hook_cover.jpg"):
        request = urllib.request.Request(candidate, method="HEAD")
        request.add_header("User-Agent", "real-estate-agent6-publisher/1.0")
        try:
            with urllib.request.urlopen(request, timeout=15) as resp:
                if resp.status == 200:
                    return candidate
        except (urllib.error.URLError, OSError):
            continue
    return None


def resolve_carousel_urls(gallery_url: str, max_images: int) -> list[str]:
    if max_images <= 0:
        return []
    urls = parse_gallery_image_urls(gallery_url)
    cover = hook_cover_url(gallery_url)
    if cover:
        urls = [cover] + [u for u in urls if u != cover]
    return urls[:max_images]


def designed_carousel_urls(page: dict[str, Any], fields: dict[str, str],
                           max_images: int) -> list[str]:
    """Слайды дизайн-карусели Агента 3 (R2 {id}/carousel/, колонка carousel_url).

    Слайды уже отобраны куратором и оформлены (хук первым, бейджи,
    колонтитул) — порядок из галереи сохраняем, диверсификация не нужна.
    Пусто/ошибка → публикатор откатится на сырые фото из «Фото».
    """
    if max_images <= 0:
        return []
    field_name = fields.get("carousel_url", "carousel_url")
    url = get_prop(page, field_name, "url")
    if not url:
        return []
    try:
        urls = parse_gallery_image_urls(url)
    except (urllib.error.URLError, OSError) as exc:
        print(f"[carousel] галерея {url} недоступна ({exc}) — беру сырые фото",
              file=sys.stderr)
        return []
    return urls[:max_images]


def carousel_enabled_for(platform: str, config: dict[str, Any]) -> bool:
    carousel = config.get("carousel", {})
    if not carousel.get("enabled", True):
        return False
    platforms = carousel.get("platforms", [])
    return not platforms or platform in platforms


def _platform_key(platform: str) -> str:
    return "x" if platform == "twitter" else platform


def max_images_for_platform(platform: str, config: dict[str, Any]) -> int:
    carousel = config.get("carousel", {})
    by_platform = carousel.get("max_images_by_platform", {})
    key = _platform_key(platform)
    if key in by_platform:
        return int(by_platform[key])
    if platform in by_platform:
        return int(by_platform[platform])
    return int(carousel.get("max_images", 10))


def min_images_for_platform(platform: str, config: dict[str, Any]) -> int:
    carousel = config.get("carousel", {})
    by_platform = carousel.get("min_images_by_platform", {})
    key = _platform_key(platform)
    if key in by_platform:
        return int(by_platform[key])
    if platform in by_platform:
        return int(by_platform[platform])
    return 1


def upload_video_for_post(platform: str, carousel_urls: list[str], config: dict[str, Any]) -> bool:
    carousel = config.get("carousel", {})
    image_only = set(carousel.get("image_only_when_carousel", []))
    if carousel_urls and platform in image_only:
        return False
    return True


def metricool_search_locations(query: str) -> list[dict[str, Any]]:
    """Search places via Metricool (Facebook location index, works for IG tagging)."""
    url = metricool_url("/actions/facebook/search-location")
    result = req("GET", url, metricool_headers())
    if not isinstance(result, list):
        return []
    q = query.lower()
    tokens = [t for t in re.split(r"[\s,]+", q) if len(t) > 2]
    if not tokens:
        return result[:20]
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in result:
        name = (item.get("name") or "").lower()
        score = sum(1 for t in tokens if t in name)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:10]] or result[:10]


def build_caption(page: dict[str, Any], fields: dict[str, str], platform: str, config: dict[str, Any]) -> str:
    bundle = build_metricool_caption_bundle(
        page, fields, platform, config, get_prop, metricool_search_locations
    )
    return bundle["text"]


def metricool_normalize_media(media_url: str) -> str:
    url = metricool_url("/actions/normalize/image/url", {"url": media_url})
    result = req("GET", url, metricool_headers())
    if isinstance(result, str) and result.startswith("http"):
        return result
    if isinstance(result, dict):
        for key in ("url", "mediaUrl", "normalizedUrl", "normalized_url"):
            if result.get(key):
                return str(result[key])
    raise RuntimeError(f"Metricool normalize failed for {media_url}: {result}")


def publication_date(scheduled_time: str, tz_name: str) -> dict[str, str]:
    if scheduled_time.endswith("Z"):
        dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(scheduled_time)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ZoneInfo(tz_name))
    return {"dateTime": local.strftime("%Y-%m-%dT%H:%M:%S"), "timezone": tz_name}


def caption_to_title(caption: str, limit: int = 95) -> str:
    """Первая содержательная строка подписи → заголовок (YouTube требует ytTitle)."""
    for line in caption.splitlines():
        line = re.sub(r"[#@]\S+", "", line).strip(" \t-—•|")
        if line:
            return line[:limit].strip()
    return "Real Estate Video"


def build_network_data(
    platform: str,
    *,
    upload_video: bool,
    has_carousel: bool,
    config: dict[str, Any],
    title: str = "",
) -> dict[str, Any]:
    mc = config.get("metricool_platform_settings", {})
    network = network_for(platform)
    data: dict[str, Any] = {}

    if network == "instagram":
        ig_type = "REEL" if upload_video else "POST"
        data["instagramData"] = {**mc.get("instagram", {}), "type": ig_type, "autoPublish": True}
    elif network == "facebook":
        fb_type = "REEL" if upload_video else "POST"
        data["facebookData"] = {**mc.get("facebook", {}), "type": fb_type}
    elif network == "tiktok":
        tk = dict(mc.get("tiktok", {"privacyOption": "PUBLIC_TO_EVERYONE"}))
        if upload_video:
            # Metricool 400: autoAddMusic/photoCoverIndex разрешены только для фото-постов
            tk.pop("autoAddMusic", None)
            tk.pop("photoCoverIndex", None)
        elif has_carousel:
            tk.setdefault("autoAddMusic", True)
            tk.setdefault("photoCoverIndex", 0)
        data["tiktokData"] = tk
    elif network == "youtube":
        yt = dict(mc.get("youtube", {"type": "short", "privacy": "public", "madeForKids": False}))
        if not yt.get("title"):
            # Metricool 400: ytTitle не может быть пустым
            yt["title"] = title or "Real Estate Video"
        data["youtubeData"] = yt
    elif network == "twitter":
        data["twitterData"] = mc.get("twitter", {"tags": []})
    elif network == "threads":
        data["threadsData"] = mc.get("threads", {})
    elif network == "linkedin":
        data["linkedinData"] = mc.get("linkedin", {"type": "post"})

    if not upload_video and not has_carousel and network in {"instagram", "tiktok", "youtube"}:
        raise ValueError(f"{platform} requires video or carousel media")

    return data


def metricool_schedule_post(
    platform: str,
    caption: str,
    scheduled_time: str,
    video_url: str | None,
    carousel_urls: list[str],
    upload_video: bool,
    config: dict[str, Any],
    *,
    first_comment: str = "",
    location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tz_name = metricool_timezone(config)
    media: list[str] = []

    if upload_video and video_url:
        media.append(metricool_normalize_media(video_url))
    for image_url in carousel_urls:
        media.append(metricool_normalize_media(image_url))

    if not media:
        raise ValueError("No media to publish — need video or carousel images")

    body: dict[str, Any] = {
        "text": caption,
        "firstCommentText": first_comment or "",
        "autoPublish": True,
        "draft": False,
        "providers": [{"network": network_for(platform)}],
        "publicationDate": publication_date(scheduled_time, tz_name),
        "media": media,
        **build_network_data(
            platform,
            upload_video=upload_video,
            has_carousel=bool(carousel_urls),
            config=config,
            title=caption_to_title(caption),
        ),
    }
    if location:
        body["location"] = location

    result = req(
        "POST",
        metricool_url("/v2/scheduler/posts"),
        metricool_headers(),
        json.dumps(body).encode("utf-8"),
    )

    if isinstance(result, dict):
        payload = result.get("data") if isinstance(result.get("data"), dict) else result
        post_id = payload.get("id") or payload.get("postId")
        post_uuid = payload.get("uuid") or payload.get("postUuid")
        if post_id is not None:
            return {
                "id": str(post_id),
                "uuid": str(post_uuid) if post_uuid else None,
                "response": result,
            }
    raise RuntimeError(f"Metricool schedule failed: {result}")


def metricool_get_post(post_id: str) -> dict[str, Any]:
    result = req("GET", metricool_url(f"/v2/scheduler/posts/{post_id}"), metricool_headers())
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        return result["data"]
    return result if isinstance(result, dict) else {}


def _looks_like_post_url(value: str, network: str) -> bool:
    if not value.startswith("http"):
        return False
    lowered = value.lower()
    hints = NETWORK_URL_HINTS.get(network, ())
    if hints and not any(hint in lowered for hint in hints):
        return False
    blocked = ("metricool.com", "cloudflare", "r2.dev", ".mp4", ".jpg", ".jpeg", ".png", ".webp")
    if any(part in lowered for part in blocked):
        return False
    if network == "facebook":
        if FACEBOOK_PLACE_ONLY.match(value):
            return False
        return any(marker in lowered for marker in FACEBOOK_POST_MARKERS)
    if network == "instagram" and "/explore/locations/" in lowered:
        return False
    return True


def metricool_planner_post_url(
    post_uuid: str | None,
    config: dict[str, Any] | None = None,
    *,
    post_id: str | None = None,
) -> str:
    """Ссылка на отложенный пост в Metricool planner (Copy link в календаре)."""
    base = "https://app.metricool.com/planner/calendar"
    if config:
        base = config.get("metricool", {}).get("planner_calendar_url", base)
    uuid = (post_uuid or "").strip()
    if not uuid and post_id:
        try:
            post = metricool_get_post(post_id)
            uuid = str(post.get("uuid") or post.get("postUuid") or "").strip()
        except RuntimeError:
            uuid = ""
    blog_id = os.environ.get("METRICOOL_BLOG_ID", "")
    params = {"blogId": blog_id, "openWithPostUuid": uuid}
    return f"{base}?{urllib.parse.urlencode(params)}"


def _unwrap_metricool_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def extract_provider_public_url(payload: Any, network: str) -> str | None:
    root = _unwrap_metricool_payload(payload)
    if not isinstance(root, dict):
        return None
    providers = root.get("providers")
    if not isinstance(providers, list):
        return None
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        provider_network = provider.get("network")
        if provider_network != network and network_for(str(provider_network or "")) != network:
            continue
        for key in ("publicUrl", "id"):
            value = provider.get(key)
            if isinstance(value, str) and _looks_like_post_url(value, network):
                return value
    return None


def extract_post_url(payload: Any, network: str) -> str | None:
    direct = extract_provider_public_url(payload, network)
    if direct:
        return direct

    root = _unwrap_metricool_payload(payload)
    candidates: list[str] = []

    def walk(obj: Any, parent_key: str | None = None) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in SKIP_URL_PARENT_KEYS and isinstance(value, (dict, list)):
                    continue
                if isinstance(value, str) and _looks_like_post_url(value, network):
                    key_lower = key.lower()
                    if any(hint in key_lower for hint in URL_KEY_HINTS) or "post" in key_lower:
                        candidates.append(value)
                walk(value, key)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, parent_key)

    walk(root)
    if candidates:
        return candidates[0]

    def walk_any(obj: Any, parent_key: str | None = None) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in SKIP_URL_PARENT_KEYS and isinstance(value, (dict, list)):
                    continue
                walk_any(value, key)
        elif isinstance(obj, list):
            for item in obj:
                walk_any(item, parent_key)
        elif isinstance(obj, str) and _looks_like_post_url(obj, network):
            candidates.append(obj)

    walk_any(root)
    return candidates[0] if candidates else None


def resolve_published_post_url(post_id: str, network: str, schedule_response: Any) -> str | None:
    url = extract_post_url(schedule_response, network)
    if url:
        return url
    try:
        return extract_post_url(metricool_get_post(post_id), network)
    except RuntimeError:
        return None


def published_url_field(
    platform: str,
    config: dict[str, Any],
    *,
    upload_video: bool = False,
    mode: str | None = None,
) -> str | None:
    mapping = config.get("notion", {}).get("published_url_fields", {})
    if network_for(platform) == "instagram":
        if upload_video or mode == "video":
            return mapping.get("instagram_reel")
        return mapping.get("instagram_carousel")
    return mapping.get(platform) or mapping.get(network_for(platform))


def instagram_post_kind(*, upload_video: bool, mode: str | None = None) -> str:
    if upload_video or mode == "video":
        return "reel"
    return "carousel"


def notion_url_property(url: str) -> dict[str, Any]:
    return {"url": url}


def post_id_field_name(fields: dict[str, str]) -> str:
    return fields.get("metricool_post_id") or fields.get("publora_post_group_id", nfc.METRICOOL_POST_ID)


def shift_schedule(scheduled_time: str, hours: float) -> str:
    dt = parse_scheduled_time_utc(scheduled_time) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def platform_jobs(
    platform: str, scheduled_time: str, config: dict[str, Any], mode: str | None
) -> list[tuple[str | None, str]]:
    """Список публикаций (mode, scheduled_time) для платформы.

    Instagram в auto-режиме получает ДВА поста: карусель в назначенное время
    и рил через instagram.reel_delay_hours (по умолчанию 4 ч) — Metricool
    не может выложить их одним постом, а вместе в один момент их постить
    не стоит (алгоритм IG режет охват одновременных публикаций).
    """
    if network_for(platform) == "instagram" and mode is None:
        delay_hours = float((config.get("instagram") or {}).get("reel_delay_hours", 4))
        return [
            ("carousel", scheduled_time),
            ("video", shift_schedule(scheduled_time, delay_hours)),
        ]
    return [(mode, scheduled_time)]


def default_schedule_time(minutes_ahead: int = 30) -> str:
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_scheduled_time_utc(scheduled_time: str) -> datetime:
    if scheduled_time.endswith("Z"):
        dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(scheduled_time)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    return dt


def spawn_deferred_post_url_sync(
    page_id: str,
    post_id: str,
    platform: str,
    scheduled_time: str,
    config: dict[str, Any],
    *,
    post_kind: str | None = None,
) -> dict[str, Any]:
    delay = int(config.get("metricool", {}).get("url_sync_delay_minutes", 5))
    script = package_root() / "scripts" / "deferred_post_url_sync.py"
    log_dir = package_root() / "data" / "deferred_sync"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{post_id}_{platform}.log"
    cmd = [
        sys.executable,
        str(script),
        "--page-id",
        page_id,
        "--post-id",
        post_id,
        "--platform",
        platform,
        "--delay-minutes",
        str(delay),
    ]
    if scheduled_time:
        cmd.extend(["--scheduled-time", scheduled_time])
    if post_kind:
        cmd.extend(["--post-kind", post_kind])
    target_note = scheduled_time or f"from_metricool_post:{post_id}"
    with log_file.open("a", encoding="utf-8") as log:
        log.write(
            f"\n--- spawn {datetime.now(timezone.utc).isoformat()} "
            f"page={page_id} post={post_id} platform={platform} "
            f"schedule={target_note} delay_min={delay} ---\n"
        )
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(package_root()),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
    return {
        "spawned": True,
        "pid": proc.pid,
        "log_file": str(log_file),
        "delay_minutes": delay,
        "scheduled_time": scheduled_time or None,
        "post_kind": post_kind,
    }


def spawn_deferred_chatplace_funnel(
    page_id: str,
    platform: str,
    scheduled_time: str,
    config: dict[str, Any],
    *,
    post_id: str | None = None,
    post_kind: str | None = None,
) -> dict[str, Any] | None:
    try:
        from setup_chatplace_funnel import should_run_chatplace
    except ImportError:
        return None

    upload_video = post_kind == "reel"
    mode = "video" if post_kind == "reel" else "carousel" if post_kind == "carousel" else None
    if not should_run_chatplace(platform, config, upload_video=upload_video, mode=mode):
        return None

    delay = int(config.get("chatplace", {}).get("delay_minutes_after_publish", 15))
    script = package_root() / "scripts" / "deferred_chatplace_funnel.py"
    log_dir = package_root() / "data" / "chatplace_jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / (
        f"{page_id}_{platform}_{post_kind}.log" if post_kind else f"{page_id}_{platform}.log"
    )
    cmd = [
        sys.executable,
        str(script),
        "--page-id",
        page_id,
        "--platform",
        platform,
        "--delay-minutes",
        str(delay),
    ]
    if post_id:
        cmd.extend(["--post-id", post_id])
    if scheduled_time:
        cmd.extend(["--scheduled-time", scheduled_time])
    if post_kind:
        cmd.extend(["--post-kind", post_kind])
    with log_file.open("a", encoding="utf-8") as log:
        log.write(
            f"\n--- chatplace spawn {datetime.now(timezone.utc).isoformat()} "
            f"page={page_id} platform={platform} delay_min={delay} ---\n"
        )
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(package_root()),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
    return {
        "spawned": True,
        "pid": proc.pid,
        "log_file": str(log_file),
        "delay_minutes": delay,
        "platform": platform,
        "post_kind": post_kind,
    }


def spawn_chatplace_if_reel_url_ready(
    page_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Отложенная ChatPlace-воронка reel, когда post_url_instagram_reel уже в Notion."""
    try:
        from setup_chatplace_funnel import (
            is_chatplace_kind_done,
            is_live_social_url,
            should_run_chatplace,
        )
    except ImportError:
        return None

    cfg = config or load_config()
    if not should_run_chatplace("instagram", cfg, upload_video=True, mode="video"):
        return None

    page = notion_get_page(page_id)
    if is_chatplace_kind_done(page, cfg, "instagram", "reel"):
        return None

    fields = cfg["notion"]["fields"]
    published = cfg["notion"]["published_url_fields"]
    reel_field = published.get("instagram_reel", nfc.POST_URL_INSTAGRAM_REEL)
    reel_url = get_prop(page, reel_field, "url")
    if not is_live_social_url(reel_url, "instagram"):
        return None

    # Пауза от момента появления ссылки (не от Metricool publicationDate)
    scheduled = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return spawn_deferred_chatplace_funnel(
        page_id,
        "instagram",
        scheduled,
        cfg,
        post_kind="reel",
    )


def publish_one(
    page_id: str,
    platform: str,
    scheduled_time: str,
    dry_run: bool,
    config: dict[str, Any],
    *,
    force: bool = False,
    bypass_lock: bool = False,
    mode: str | None = None,
) -> dict[str, Any]:
    if not metricool_enabled(config):
        return {
            "page_id": page_id,
            "platform": platform,
            "mode": mode or "auto",
            "skipped": True,
            "reason": "metricool_disabled",
            "hint": "Публикация в соцсети — через Publisher social (телефон).",
        }

    cfg_notion = config["notion"]
    fields = cfg_notion["fields"]
    mapping = config["video_format_by_platform"]

    page = notion_get_page(page_id)

    current_status = get_prop(page, fields["status"], "status")
    try:
        video_url, video_field = pick_video_url(page, fields, platform, mapping)
    except ValueError:
        if mode != "carousel":
            raise
        # Для чисто карусельного поста видео не обязательно
        video_url, video_field = None, None
    caption_bundle = build_metricool_caption_bundle(
        page, fields, platform, config, get_prop, metricool_search_locations
    )
    caption = caption_bundle["text"]

    photo_field = fields.get("photo", nfc.PHOTO)
    gallery_url = get_prop(page, photo_field, "url")
    carousel_cfg = config.get("carousel", {})
    carousel_urls: list[str] = []
    if carousel_enabled_for(platform, config):
        max_img = max_images_for_platform(platform, config)
        min_img = min_images_for_platform(platform, config)
        # Приоритет — дизайн-слайды Агента 3 (carousel_url); фолбэк — сырые фото
        carousel_urls = designed_carousel_urls(page, fields, max_img)
        if carousel_urls:
            print(f"[carousel] дизайн-слайды Агента 3: {len(carousel_urls)} шт.")
        elif gallery_url:
            all_urls = parse_gallery_image_urls(gallery_url)
            carousel_urls, _ = select_diverse_images(all_urls, max_img, config, context="carousel")
        if mode != "video" and carousel_urls and len(carousel_urls) < min_img:
            raise ValueError(
                f"{platform} needs at least {min_img} images, got {len(carousel_urls)} (max {max_img})"
            )

    if mode == "carousel" and not carousel_urls:
        return {
            "page_id": page_id,
            "platform": platform,
            "mode": mode,
            "skipped": True,
            "reason": "no_carousel_images",
        }

    if mode == "carousel":
        upload_video = False
    elif mode == "video":
        carousel_urls = []
        upload_video = True
    else:
        upload_video = upload_video_for_post(platform, carousel_urls, config)

    skip_reason = publish_skip_reason(
        page,
        fields,
        platform,
        config,
        upload_video=upload_video,
        mode=mode,
        bypass_lock=bypass_lock,
        force=force,
    )
    if skip_reason:
        return {
            "page_id": page_id,
            "platform": platform,
            "mode": mode,
            "skipped": True,
            "reason": skip_reason,
        }

    from daily_quota import find_free_day

    quota_day_offset, quota_reason_today = find_free_day(
        platform, config, upload_video=upload_video, mode=mode, force=force,
    )
    if quota_day_offset is None:
        return {
            "page_id": page_id,
            "platform": platform,
            "mode": mode,
            "skipped": True,
            "reason": f"{quota_reason_today} (нет свободного дня в пределах lookahead)",
        }
    if quota_day_offset > 0:
        # Квота на сегодня занята — не пропускаем, а планируем на первый свободный день
        scheduled_time = shift_schedule(scheduled_time, hours=24 * quota_day_offset)
        print(
            f"[quota] {platform}: {quota_reason_today} → пост запланирован на "
            f"+{quota_day_offset} дн. ({scheduled_time})"
        )

    result = {
        "page_id": page_id,
        "platform": platform,
        "metricool_network": network_for(platform),
        "video_field": video_field,
        "video_url": video_url,
        "gallery_url": gallery_url,
        "carousel_images": len(carousel_urls),
        "carousel_max_images": max_images_for_platform(platform, config) if carousel_urls else 0,
        "carousel_urls_preview": carousel_urls[:3],
        "upload_video": upload_video,
        "mode": mode or "auto",
        "caption_preview": caption[:120],
        "caption_source_field": caption_bundle.get("caption_source_field"),
        "hashtags_preview": (caption_bundle.get("hashtags") or "")[:120],
        "hashtag_placement": caption_bundle.get("hashtag_placement"),
        "location_name": (caption_bundle.get("location") or {}).get("name"),
        "first_comment_preview": (caption_bundle.get("firstCommentText") or "")[:120],
        "previous_status": current_status,
        "scheduled_time": scheduled_time,
        "timezone": metricool_timezone(config),
    }
    if quota_day_offset > 0:
        result["quota_shifted_days"] = quota_day_offset
        result["quota_reason_today"] = quota_reason_today

    if dry_run:
        result["dry_run"] = True
        return result

    status_taken = resolve_status("taken", config)
    status_scheduled = resolve_status("scheduled", config)
    status_failed = resolve_status("failed", config)

    applied_taken = notion_update_status_safe(
        page_id, fields["status"], status_taken, fallback="video_in_progress"
    )
    result["notion_status_taken"] = applied_taken

    taken_props: dict[str, Any] = {
        fields["status"]: {"status": {"name": applied_taken}},
    }
    lock_field = fields.get("agent6_locked")
    if lock_field:
        taken_props[lock_field] = notion_checkbox_property(True)
    taken_at_field = fields.get("agent6_taken_at")
    if taken_at_field and not get_prop(page, taken_at_field, "date"):
        taken_props[taken_at_field] = notion_date_property(utc_today_iso())
    notion_update_fields(page_id, taken_props)

    try:
        scheduled = metricool_schedule_post(
            platform,
            caption,
            scheduled_time,
            video_url if upload_video else None,
            carousel_urls,
            upload_video,
            config,
            first_comment=caption_bundle.get("firstCommentText", ""),
            location=caption_bundle.get("location"),
        )
        post_id = scheduled["id"]
        post_uuid = scheduled.get("uuid")
        network = network_for(platform)
        planner_url = metricool_planner_post_url(post_uuid, config, post_id=post_id)
        published_url = resolve_published_post_url(post_id, network, scheduled["response"])
        url_to_save = published_url or planner_url

        pg_field = post_id_field_name(fields)
        err_field = fields.get("publish_error", nfc.LAST_ERROR)
        notion_props: dict[str, Any] = {
            fields["status"]: {"status": {"name": status_scheduled}},
            pg_field: {"rich_text": [{"text": {"content": post_id}}]},
            err_field: {"rich_text": []},
        }
        if carousel_urls and fields.get("agent6_carousel_done"):
            notion_props[fields["agent6_carousel_done"]] = notion_checkbox_property(True)
        if upload_video and video_url and fields.get("agent6_video_done"):
            notion_props[fields["agent6_video_done"]] = notion_checkbox_property(True)
        url_field = published_url_field(
            platform, config, upload_video=upload_video, mode=mode
        )
        if url_field and url_to_save:
            notion_props[url_field] = notion_url_property(url_to_save)
        publish_at_field = fields.get("publish_at") or nfc.PUBLISH_AT
        if publish_at_field and scheduled_time:
            # Календарь CRM: дата/время выхода поста (перезаписываем на фактический слот)
            notion_props[publish_at_field] = notion_datetime_property(
                scheduled_time, metricool_timezone(config)
            )

        notion_update_fields(page_id, notion_props)

        result["metricool_post_id"] = post_id
        result["planner_url"] = planner_url
        result["published_url"] = published_url
        result["saved_url"] = url_to_save
        result["published_url_field"] = url_field
        result["notion_status"] = status_scheduled
        post_kind = (
            instagram_post_kind(upload_video=upload_video, mode=mode)
            if network_for(platform) == "instagram"
            else None
        )
        if not published_url:
            result["deferred_url_sync"] = spawn_deferred_post_url_sync(
                page_id,
                post_id,
                platform,
                scheduled_time,
                config,
                post_kind=post_kind,
            )
        cp_spawn = spawn_deferred_chatplace_funnel(
            page_id,
            platform,
            scheduled_time,
            config,
            post_id=post_id,
            post_kind=post_kind,
        )
        if cp_spawn:
            result["deferred_chatplace_funnel"] = cp_spawn
        return result

    except Exception as e:
        err_field = fields.get("publish_error", nfc.LAST_ERROR)
        err_count_field = fields.get("error_count", "error_count")
        err_count = get_prop(page, err_count_field, "number") or 0
        notion_update_fields(
            page_id,
            {
                fields["status"]: {"status": {"name": status_failed}},
                err_field: {"rich_text": [{"text": {"content": str(e)[:2000]}}]},
                err_count_field: {"number": err_count + 1},
            },
        )
        raise


def main() -> int:
    load_dotenv()
    config = load_config()

    parser = argparse.ArgumentParser(description="Notion CRM → Metricool publisher")
    parser.add_argument("--page-id", help="Notion page ID")
    parser.add_argument("--platform", help="instagram|tiktok|threads|all|...")
    parser.add_argument("--schedule", help="ISO8601 UTC scheduled time")
    parser.add_argument("--queue", action="store_true", help="Process all ready records")
    parser.add_argument("--dry-run", action="store_true", help="Validate without publishing")
    parser.add_argument("--force", action="store_true", help="Publish even if agent6_locked")
    parser.add_argument(
        "--mode",
        choices=["carousel", "video", "auto"],
        default="auto",
        help="carousel=photos only, video=Seedance reel only, auto=per platform rules",
    )
    parser.add_argument(
        "--skip-schema-check",
        action="store_true",
        help="Skip Notion schema validation on start",
    )
    args = parser.parse_args()

    if not metricool_enabled(config) and not args.dry_run:
        print(
            json.dumps(
                {
                    "skipped": True,
                    "reason": "metricool_disabled",
                    "hint": "Включите metricool.enabled в config/publisher.json "
                    "или публикуйте через Publisher social (телефон).",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    run_schema_check(args.skip_schema_check)

    post_mode = None if args.mode == "auto" else args.mode

    scheduled = args.schedule or default_schedule_time()

    if args.queue:
        if not args.platform:
            parser.error("--platform required with --queue")
        database_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("NOTION_DB_ID")
        if not database_id:
            print("Set NOTION_DATABASE_ID or NOTION_DB_ID in .env", file=sys.stderr)
            return 1
        status_field = config["notion"]["fields"]["status"]
        status_ready = config["notion"]["statuses"]["ready"]
        locked_field = config["notion"]["fields"].get("agent6_locked")
        pages = notion_query_ready(
            database_id, status_ready, status_field, locked_field=locked_field
        )
        if not pages:
            print("Queue empty.")
            return 0
        for page in pages:
            pid = page["id"]
            print(f"\n--- Processing {pid} ---")
            try:
                fields = config["notion"]["fields"]
                bypass_lock = args.force or is_agent6_locked(page, fields)
                for job_mode, job_time in platform_jobs(
                    args.platform, scheduled, config, post_mode
                ):
                    out = publish_one(
                        pid,
                        args.platform,
                        job_time,
                        args.dry_run,
                        config,
                        force=args.force,
                        bypass_lock=bypass_lock,
                        mode=job_mode,
                    )
                    print(json.dumps(out, indent=2, ensure_ascii=False))
                    if not out.get("skipped") and not args.dry_run:
                        bypass_lock = True
            except Exception as e:
                print(f"ERROR {pid}: {e}", file=sys.stderr)
        return 0

    if not args.page_id and not args.queue:
        parser.error("--page-id required unless --queue")

    platforms: list[str] = []
    if args.platform == "all":
        platforms = ALL_PUBLISH_PLATFORMS.copy()
    elif args.platform:
        platforms = [args.platform]
    else:
        parser.error("--platform required")

    if args.page_id:
        fields = config["notion"]["fields"]
        page0 = notion_get_page(args.page_id)
        bypass_lock = args.force or is_agent6_locked(page0, fields)
        exit_code = 0
        for platform in platforms:
            for job_mode, job_time in platform_jobs(platform, scheduled, config, post_mode):
                try:
                    out = publish_one(
                        args.page_id,
                        platform,
                        job_time,
                        args.dry_run,
                        config,
                        force=args.force,
                        bypass_lock=bypass_lock,
                        mode=job_mode,
                    )
                except Exception as e:
                    print(f"ERROR {platform}/{job_mode or 'auto'}: {e}", file=sys.stderr)
                    exit_code = 1
                    continue
                print(json.dumps(out, indent=2, ensure_ascii=False))
                if not out.get("skipped") and not args.dry_run:
                    bypass_lock = True
        return exit_code

    parser.error("--page-id required unless --queue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
