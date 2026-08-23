from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from . import notion_client as notion
from .android.adb import AdbDevice, check_adb, push_files, reset_uiautomator
from .channels import ChannelResult, get_channel
from .config import (
    load_android_config,
    load_fb_groups,
    load_publisher_config,
    media_cache_dir,
    notion_fields,
    production_channels,
    status_ready,
)
from .dotenv_util import package_root
from .facebook_batch import (
    facebook_batch_applies,
    resolve_facebook_batch_config,
    run_facebook_phone_batch,
    summarize_facebook_batch_status,
)
from .media import (
    download_job_media,
    resolve_brand_open_home_urls,
    resolve_carousel_urls,
    resolve_image_urls,
)
from .models import CHANNELS, ListingFields, PublishJob
from .state import (
    append_log,
    channel_inflight,
    clear_channel_inflight,
    clear_channel_done,
    clear_notion_update,
    clear_scheduled,
    daily_count,
    due_scheduled,
    enqueue_notion_update,
    is_channel_done,
    invalidate_channel_post_url,
    load_state,
    mark_channel_done,
    mark_channel_inflight,
    mark_channel_verified,
    mark_notion_update_failed,
    object_has_progress,
    pending_notion_updates,
    save_job_snapshot,
    schedule_channel,
)


def _optional_rich_text(
    page: dict[str, Any], fields: dict[str, str], key: str
) -> str:
    name = fields.get(key)
    if not name:
        return ""
    return notion.get_prop(page, name, "rich_text") or ""


def _channel_url_field(channel: str, fields: dict[str, str]) -> str | None:
    mapping = {
        "tiktok": "post_url_tiktok",
        "tiktok_carousel": "post_url_tiktok_carousel",
        "instagram_reel": "post_url_instagram_reel",
        "instagram_carousel": "post_url_instagram_carousel",
        "fb_marketplace": "post_url_facebook",
        "fb_groups": "post_url_fb_groups",
        "linkedin": "post_url_linkedin",
        "twitter": "post_url_x",
        "youtube_shorts": "post_url_youtube",
    }
    key = mapping.get(channel)
    return fields.get(key) if key else None


def _orchestration_plan(config: dict[str, Any]) -> dict[str, Any]:
    orch = config.get("orchestration") or {}
    return {
        "video": list(orch.get("video_channels") or []),
        "carousel": list(orch.get("carousel_channels") or ["fb_groups"]),
        # всегда в конце, после остальных соцсетей
        "final": list(orch.get("final_channels") or ["fb_marketplace"]),
        "delayed": dict(orch.get("delayed_carousel") or {}),
    }


def _facebook_batch_wanted(
    channels: list[str] | None,
    job: PublishJob,
    config: dict[str, Any],
) -> list[str]:
    """Pending FB channels in batch order (groups before marketplace)."""
    batch_cfg = resolve_facebook_batch_config(config)
    if not batch_cfg:
        return list(channels or job.channels_pending or production_channels(config))
    batch_order = list(batch_cfg["channels"])
    requested = list(channels or job.channels_pending or production_channels(config))
    state = load_state()
    pending = [
        ch
        for ch in batch_order
        if ch in requested and not is_channel_done(state, job.object_id, ch)
    ]
    return pending or [ch for ch in batch_order if ch in requested]


def immediate_channels_for_queue(
    job: PublishJob,
    config: dict[str, Any],
    *,
    requested: list[str] | None = None,
) -> list[str]:
    """
    Normal queue runs never consume delayed carousels. An explicit --channel is
    treated as an intentional manual override; due entries use run_scheduled().
    """
    wanted = list(requested if requested is not None else job.channels_pending)
    if requested is None:
        delayed = set(_orchestration_plan(config)["delayed"])
        wanted = [channel for channel in wanted if channel not in delayed]
    if "fb_marketplace" in wanted:
        wanted = [channel for channel in wanted if channel != "fb_marketplace"] + [
            "fb_marketplace"
        ]
    return wanted


def reconcile_missing_delayed_schedules(
    page: dict[str, Any],
    job: PublishJob,
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[str]:
    """
    Recover delayed work when the parent post was completed outside this local
    state (for example, a real URL already exists in Notion).

    The original delay has necessarily elapsed or cannot be reconstructed, so
    the recovered entry is due immediately. Existing schedules and completed
    carousel channels are never overwritten.
    """
    state = load_state()
    fields = notion_fields(config)
    scheduled = state.get("scheduled") or {}
    created: list[str] = []
    publish_after = (now or datetime.now(timezone.utc)).isoformat()
    for carousel_channel, spec in _orchestration_plan(config)["delayed"].items():
        if carousel_channel not in job.channels_pending:
            continue
        key = f"{job.object_id}:{carousel_channel}"
        if key in scheduled or is_channel_done(state, job.object_id, carousel_channel):
            continue
        parent = str(spec.get("after") or "")
        parent_done = is_channel_done(state, job.object_id, parent)
        if not parent_done and parent:
            parent_done = _already_published_in_notion(page, parent, fields)
        if not parent_done:
            continue
        schedule_channel(
            state,
            object_id=job.object_id,
            page_id=job.page_id,
            channel=carousel_channel,
            publish_after=publish_after,
            after_channel=parent,
        )
        append_log(
            state,
            job.object_id,
            f"recovered due schedule {carousel_channel}: parent {parent} already published",
        )
        scheduled = state.get("scheduled") or {}
        created.append(carousel_channel)
    return created


def _local_day(
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    tz_name = str(config.get("timezone") or "Asia/Bangkok")
    try:
        from zoneinfo import ZoneInfo

        current = now or datetime.now(ZoneInfo(tz_name))
        if current.tzinfo is None:
            current = current.replace(tzinfo=ZoneInfo(tz_name))
        else:
            current = current.astimezone(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        current = now or datetime.now(timezone.utc)
    return current.date().isoformat()


def _daily_limit_result(
    state: dict[str, Any],
    channel: str,
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> ChannelResult | None:
    raw_limit = ((config.get("limits") or {}).get("per_channel_daily_max") or {}).get(
        channel
    )
    if raw_limit is None:
        return None
    limit = int(raw_limit)
    day = _local_day(config, now=now)
    count = daily_count(state, day, channel)
    if limit <= 0 or count >= limit:
        return ChannelResult(
            channel=channel,
            ok=False,
            skipped=True,
            reason=f"daily_limit_reached ({count}/{limit}, {day})",
            publication_status="skipped",
        )
    return None


def _result_publication_status(
    result: ChannelResult,
    channel: str,
    config: dict[str, Any],
    *,
    dry_run: bool,
    confirm_post: bool,
) -> str:
    valid_post_url = _is_real_post_url(result.post_url, channel)
    if result.publication_status:
        if result.publication_status == "verified" and not valid_post_url:
            optional = set(
                (config.get("verification") or {}).get("url_optional_channels")
                or ["fb_marketplace", "fb_groups"]
            )
            return "accepted" if channel in optional else "submitted_unverified"
        if (
            result.publication_status == "submitted_unverified"
            and channel
            in set(
                (config.get("verification") or {}).get("url_optional_channels")
                or ["fb_marketplace", "fb_groups"]
            )
        ):
            return "accepted"
        return result.publication_status
    if result.skipped:
        return "preview" if dry_run or not confirm_post else "skipped"
    if not result.ok:
        return "failed"
    if valid_post_url:
        return "verified"
    optional = set(
        (config.get("verification") or {}).get("url_optional_channels")
        or ["fb_marketplace", "fb_groups"]
    )
    return "accepted" if channel in optional else "submitted_unverified"


def _is_real_post_url(url: str | None, channel: str) -> bool:
    """Accept only a real post URL whose host and path match the channel."""
    if not (url or "").strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.lower()

    def on_domain(*domains: str) -> bool:
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)

    if channel in ("tiktok", "tiktok_carousel"):
        if not on_domain("tiktok.com"):
            return False
        # TikTok short links do not expose the post type until redirect.
        if host in ("vm.tiktok.com", "vt.tiktok.com"):
            return bool(path.strip("/"))
        expected_segment = "/video/" if channel == "tiktok" else "/photo/"
        return expected_segment in path
    if channel == "instagram_reel":
        return on_domain("instagram.com") and "/reel/" in path
    if channel == "instagram_carousel":
        return on_domain("instagram.com") and "/p/" in path
    if channel == "youtube_shorts":
        return (
            (on_domain("youtube.com") and "/shorts/" in path)
            or (on_domain("youtu.be") and bool(path.strip("/")))
        )
    if channel == "linkedin":
        return on_domain("linkedin.com") and (
            "/posts/" in path or "/feed/update/" in path
        )
    if channel == "twitter":
        return on_domain("x.com", "twitter.com") and "/status/" in path
    if channel in ("fb_groups", "fb_marketplace"):
        return on_domain("facebook.com", "fb.com")
    return bool(host)


def _post_url_in_notion(
    page: dict[str, Any],
    channel: str,
    fields: dict[str, str],
) -> str | None:
    url_field = _channel_url_field(channel, fields)
    if not url_field:
        return None
    if channel == "fb_groups":
        url = notion.get_prop(page, url_field, "rich_text")
    else:
        url = notion.get_prop(page, url_field, "url")
    return url if _is_real_post_url(url, channel) else None


def _already_published_in_notion(
    page: dict[str, Any],
    channel: str,
    fields: dict[str, str],
) -> bool:
    return bool(_post_url_in_notion(page, channel, fields))


def reconcile_local_completed_from_notion(
    page: dict[str, Any],
    job: PublishJob,
    config: dict[str, Any],
) -> list[str]:
    """Mirror real Notion post URLs into local state without affecting daily limits."""
    fields = notion_fields(config)
    state = load_state()
    reconciled: list[str] = []
    for channel in production_channels(config):
        if is_channel_done(state, job.object_id, channel):
            continue
        url = _post_url_in_notion(page, channel, fields)
        if not url:
            continue
        mark_channel_verified(
            state,
            job.object_id,
            channel,
            post_url=url,
        )
        append_log(
            state,
            job.object_id,
            f"reconciled {channel}=verified from real Notion URL",
        )
        reconciled.append(channel)
    return reconciled


def pending_channels_for_page(
    page: dict[str, Any],
    *,
    object_id: str,
    config: dict[str, Any],
    channels: list[str] | None = None,
) -> list[str]:
    fields = notion_fields(config)
    state = load_state()
    wanted = list(channels or production_channels(config))
    # Facebook Marketplace — всегда последним среди pending
    if "fb_marketplace" in wanted:
        wanted = [c for c in wanted if c != "fb_marketplace"] + ["fb_marketplace"]
    pending: list[str] = []
    for ch in wanted:
        if is_channel_done(state, object_id, ch):
            continue
        if _already_published_in_notion(page, ch, fields):
            continue
        pending.append(ch)
    return pending


def build_job_from_page(
    page: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    channels: list[str] | None = None,
) -> PublishJob:
    cfg = config or load_publisher_config()
    fields = notion_fields(cfg)
    media_cfg = cfg.get("media", {})

    object_id = notion.get_prop(page, fields["object_id"], "rich_text") or page["id"]
    title = notion.get_prop(page, fields["title"], "title") or object_id
    carousel = notion.get_prop(page, fields.get("carousel_url", ""), "url") if fields.get("carousel_url") else None
    gallery = notion.get_prop(page, fields["photo"], "url")
    video = notion.get_prop(page, fields["video_url_seedance"], "url")
    if not video:
        video = notion.get_prop(page, fields["video_url_vertical"], "url")

    # Карусели — дизайн-слайды carousel_url; slide_01 = ценовой хук, порядок важен.
    images: list[str] = []
    images_source = "none"
    if carousel:
        images = resolve_carousel_urls(carousel)
        images_source = "carousel_url"
    elif media_cfg.get("allow_raw_gallery_fallback"):
        max_images = max(
            int(media_cfg.get("max_carousel_images", 10)),
            int(media_cfg.get("max_groups_images", 10)),
        )
        images = resolve_image_urls(
            gallery or "",
            max_images,
            hook_cover_first=bool(media_cfg.get("hook_cover_first", True)),
        )
        images_source = "photo_raw"

    # FB Marketplace ONLY — отдельная галерея brand_open_home_url
    mp_cfg = cfg.get("fb_marketplace") or {}
    brand_field = fields.get("brand_open_home_url") or "brand_open_home_url"
    brand_url = notion.get_prop(page, brand_field, "url") or ""
    mp_max = int(
        mp_cfg.get("max_images")
        or media_cfg.get("max_marketplace_images")
        or 8
    )
    marketplace_images = resolve_brand_open_home_urls(brand_url, mp_max)

    pending = pending_channels_for_page(
        page, object_id=object_id, config=cfg, channels=channels
    )

    listing = ListingFields(
        title=title,
        housing_type=notion.get_prop(page, fields["housing_type"], "select"),
        rooms=notion.get_prop(page, fields["rooms"], "number"),
        bathrooms=notion.get_prop(page, fields["bathrooms"], "number"),
        price_monthly=notion.get_prop(page, fields["price_monthly"], "number"),
        district=notion.get_prop(page, fields["district"], "rich_text"),
        address=notion.get_prop(page, fields["address"], "rich_text"),
        google_maps=notion.get_prop(page, fields["google_maps"], "url"),
    )

    return PublishJob(
        page_id=page["id"],
        object_id=object_id,
        title=title,
        caption_social=notion.get_prop(page, fields["caption_social"], "rich_text") or "",
        caption_x=_optional_rich_text(page, fields, "caption_x"),
        caption_fb=notion.get_prop(page, fields["caption_fb"], "rich_text") or "",
        cta_instagram=notion.get_prop(page, fields["cta_instagram"], "rich_text") or "",
        title_youtube_shorts=_optional_rich_text(page, fields, "title_youtube_shorts"),
        caption_youtube_shorts=_optional_rich_text(page, fields, "caption_youtube_shorts"),
        video_url=video,
        image_urls=images,
        images_source=images_source,
        marketplace_image_urls=marketplace_images,
        listing=listing,
        fb_groups=load_fb_groups(),
        channels_pending=pending,
    )


def prepare_job(
    job: PublishJob,
    *,
    push_to_device: bool = False,
    push_marketplace: bool = True,
    android_cfg: dict[str, Any] | None = None,
) -> PublishJob:
    cfg = load_publisher_config()
    cache = media_cache_dir(cfg)
    mp_cfg = cfg.get("fb_marketplace") or {}
    local_video, local_images, local_mp = download_job_media(
        object_id=job.object_id,
        video_url=job.video_url,
        image_urls=job.image_urls,
        cache_dir=cache,
        marketplace_image_urls=job.marketplace_image_urls,
    )
    job.local_video = local_video
    job.local_images = local_images
    job.local_marketplace_images = local_mp

    if push_to_device:
        acfg = android_cfg or load_android_config()
        status = check_adb(acfg)
        if not status.get("ok"):
            raise RuntimeError(status.get("error") or "adb not ready")
        device = AdbDevice(
            serial=status.get("serial") or acfg.get("serial"),
            adb_bin=acfg.get("adb_bin", "adb"),
            media_dir=acfg.get("media_dir", "/sdcard/Download/publisher_social"),
        )
        remote_video, remote_images, remote_mp = push_files(
            device,
            local_video=local_video,
            local_images=local_images,
            object_id=job.object_id,
            local_marketplace_images=local_mp if push_marketplace else None,
            marketplace_media_dir=mp_cfg.get("device_media_dir")
            or "/sdcard/Download/brand_open_home",
        )
        job.device_video = remote_video
        job.device_images = remote_images
        job.device_marketplace_images = remote_mp

    jobs_dir = package_root() / "data" / "jobs"
    save_job_snapshot(job.to_dict(), jobs_dir)
    return job


def min_minutes_between_posts(config: dict[str, Any] | None = None) -> int:
    cfg = config or load_publisher_config()
    return int(cfg.get("limits", {}).get("min_minutes_between_posts", 120))


def _parse_notion_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # date-only → начало дня UTC (Notion без времени)
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def notion_object_cooldown_status(
    page: dict[str, Any],
    *,
    config: dict[str, Any],
    state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """
    Пауза между объектами по колонке Notion «Дата и время публикации».
    Если у текущего объекта колонка уже заполнена или есть локальный прогресс —
    можно продолжать. Иначе смотрим самую свежую дату у другого объекта.
    """
    gap = min_minutes_between_posts(config)
    if gap <= 0:
        return True, ""

    fields = notion_fields(config)
    published_field = fields.get("published_at") or "Дата и время публикации"
    object_id_field = fields.get("object_id") or "Объект ID"
    object_id = notion.get_prop(page, object_id_field, "rich_text") or page.get("id", "")

    st = state if state is not None else load_state()
    own_published = notion.get_prop(page, published_field, "date")
    if own_published or object_has_progress(st, str(object_id)):
        return True, ""

    latest_pages = notion.query_latest_published(
        notion.database_id(),
        published_field,
        page_size=3,
    )
    last_page = None
    last_id = ""
    last_at_raw = ""
    for cand in latest_pages:
        cand_id = notion.get_prop(cand, object_id_field, "rich_text") or ""
        cand_at = notion.get_prop(cand, published_field, "date") or ""
        if not cand_at:
            continue
        if cand_id and cand_id == object_id:
            continue
        if cand.get("id") == page.get("id"):
            continue
        last_page = cand
        last_id = cand_id or cand.get("id", "")[:8]
        last_at_raw = cand_at
        break

    if last_page is None or not last_at_raw:
        return True, ""

    last_at = _parse_notion_dt(last_at_raw)
    if last_at is None:
        return True, ""
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)

    now_dt = now or datetime.now(timezone.utc)
    elapsed = now_dt - last_at
    need = timedelta(minutes=gap)
    if elapsed >= need:
        return True, ""

    remain = need - elapsed
    remain_min = int(remain.total_seconds() // 60) + 1
    ready_at = (last_at + need).isoformat()
    return (
        False,
        f"пауза между объектами: последний {last_id} в «{published_field}»={last_at_raw}; "
        f"ждём ещё ~{remain_min} мин (готово после {ready_at}; минимум {gap} мин)",
    )


def _maybe_write_published_at(job: PublishJob, config: dict[str, Any]) -> None:
    """Пишем «Дата и время публикации» один раз — при первом успешном посте объекта."""
    fields = notion_fields(config)
    published_field = fields.get("published_at")
    if not published_field:
        return
    try:
        page = notion.get_page(job.page_id)
    except Exception as e:  # noqa: BLE001
        append_log(load_state(), job.object_id, f"published_at read failed: {e}")
        return
    if notion.get_prop(page, published_field, "date"):
        return

    tz_name = str(config.get("timezone") or "Asia/Bangkok")
    try:
        from zoneinfo import ZoneInfo

        local_now = datetime.now(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        local_now = datetime.now(timezone.utc)
        tz_name = "UTC"

    # Notion: либо offset в start, либо time_zone — не оба сразу
    start_iso = local_now.replace(microsecond=0, tzinfo=None).isoformat(timespec="seconds")
    state = load_state()
    key = f"{job.page_id}:_published_at"
    enqueue_notion_update(
        state,
        key=key,
        page_id=job.page_id,
        object_id=job.object_id,
        channel="_published_at",
        properties={
            published_field: notion.date_prop(start_iso, time_zone=tz_name),
        },
    )
    ok, _ = retry_pending_notion_updates(only_keys={key})
    if ok:
        append_log(
            load_state(),
            job.object_id,
            f"notion {published_field}={start_iso}",
        )


def fetch_ready_pages(
    *,
    page_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    cfg = load_publisher_config()
    fields = notion_fields(cfg)
    if page_id:
        return [notion.get_page(page_id)]
    pages = notion.query_ready(
        notion.database_id(),
        status_ready(cfg),
        fields["status"],
    )
    max_n = limit if limit is not None else int(cfg.get("limits", {}).get("max_objects_per_run", 1))
    return pages[:max_n]


def fetch_queue_pages(
    *,
    limit: int | None = None,
    channels: list[str] | None = None,
    reconcile_scheduled: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Очередь для `queue` с паузой по Notion «Дата и время публикации».
    """
    cfg = load_publisher_config()
    fields = notion_fields(cfg)
    pages = notion.query_ready(
        notion.database_id(),
        status_ready(cfg),
        fields["status"],
    )
    max_n = limit if limit is not None else int(cfg.get("limits", {}).get("max_objects_per_run", 1))
    state = load_state()
    selected: list[dict[str, Any]] = []
    skipped_msgs: list[str] = []
    for page in pages:
        job = build_job_from_page(page, config=cfg, channels=channels)
        if reconcile_scheduled and channels is None:
            reconcile_local_completed_from_notion(page, job, cfg)
            recovered = reconcile_missing_delayed_schedules(page, job, cfg)
            if recovered:
                skipped_msgs.append(
                    f"{job.object_id}: восстановлены due scheduled: {','.join(recovered)}"
                )
        immediate = immediate_channels_for_queue(job, cfg, requested=channels)
        if not immediate:
            if job.channels_pending:
                skipped_msgs.append(
                    f"{job.object_id}: только отложенные каналы; ждём scheduled-срок"
                )
            continue
        ok, msg = notion_object_cooldown_status(page, config=cfg, state=state)
        if not ok:
            skipped_msgs.append(f"{job.object_id}: {msg}")
            continue
        selected.append(page)
        if len(selected) >= max_n:
            break
    return selected, skipped_msgs


def _execute_channel(
    job: PublishJob,
    ch: str,
    *,
    dry_run: bool,
    confirm_post: bool,
    cfg: dict[str, Any],
    android_cfg: dict[str, Any],
    state: dict[str, Any],
) -> ChannelResult:
    inflight = channel_inflight(state, job.object_id, ch)
    if inflight:
        return ChannelResult(
            channel=ch,
            ok=False,
            skipped=True,
            reason=(
                "manual_verification_required: previous live attempt was "
                f"interrupted at {inflight.get('started_at') or 'unknown time'}"
            ),
            publication_status="submitted_unverified",
        )
    if confirm_post and not dry_run:
        limited = _daily_limit_result(state, ch, cfg)
        if limited:
            append_log(state, job.object_id, f"{ch}: {limited.reason}")
            return limited

    channel = get_channel(ch)
    if confirm_post and not dry_run:
        mark_channel_inflight(state, job.object_id, ch)
    try:
        result = channel.publish(
            job,
            dry_run=dry_run,
            android_cfg=android_cfg,
            publisher_cfg=cfg,
            confirm_post=confirm_post,
        )
    except Exception as exc:  # noqa: BLE001
        result = ChannelResult(channel=ch, ok=False, reason=str(exc))
    if result.post_url and not _is_real_post_url(result.post_url, ch):
        invalid_url_note = "captured URL did not match the published channel"
        result.post_url = None
        result.note = f"{(result.note or '').rstrip()}; {invalid_url_note}".strip("; ")
    result.publication_status = _result_publication_status(
        result,
        ch,
        cfg,
        dry_run=dry_run,
        confirm_post=confirm_post,
    )
    append_log(state, job.object_id, f"{ch}: {result.reason or result.note or result.ok}")
    if result.ok and not result.skipped and not dry_run:
        first_success = not object_has_progress(state, job.object_id)
        newly_recorded = mark_channel_done(
            state,
            job.object_id,
            ch,
            post_url=result.post_url,
            note=result.note,
            status=result.publication_status,
            daily_date=_local_day(cfg) if confirm_post else None,
        )
        clear_scheduled(state, object_id=job.object_id, channel=ch)
        _try_write_notion_result(job, ch, result)
        if first_success and newly_recorded:
            _maybe_write_published_at(job, cfg)
        if newly_recorded:
            _maybe_schedule_delayed_carousel(state, job, ch, cfg)
    elif confirm_post and not dry_run:
        clear_channel_inflight(state, job.object_id, ch)
    return result


def run_channels(
    job: PublishJob,
    *,
    channels: list[str] | None = None,
    dry_run: bool = True,
    push_media: bool = False,
    confirm_post: bool = False,
    sleep_fn=None,
) -> list[ChannelResult]:
    import time

    cfg = load_publisher_config()
    android_cfg = load_android_config()
    wanted = list(channels or job.channels_pending or production_channels(cfg))
    batch_cfg = resolve_facebook_batch_config(cfg)
    use_batch = facebook_batch_applies(cfg, wanted)

    if use_batch and batch_cfg:
        wanted = _facebook_batch_wanted(channels, job, cfg)
    elif "fb_marketplace" in wanted:
        wanted = [c for c in wanted if c != "fb_marketplace"] + ["fb_marketplace"]

    need_push = not dry_run and (push_media or confirm_post)
    job = prepare_job(
        job,
        push_to_device=need_push,
        android_cfg=android_cfg,
        push_marketplace="fb_marketplace" in wanted,
    )

    if use_batch and batch_cfg:
        state = load_state()

        def _run(ch: str) -> ChannelResult:
            return _execute_channel(
                job,
                ch,
                dry_run=dry_run,
                confirm_post=confirm_post,
                cfg=cfg,
                android_cfg=android_cfg,
                state=load_state(),
            )

        results = run_facebook_phone_batch(
            job,
            channels=wanted,
            batch_cfg=batch_cfg,
            dry_run=dry_run,
            confirm_post=confirm_post,
            execute_channel=_run,
            sleep_fn=sleep_fn or time.sleep,
        )
        print(
            f"[facebook_batch] status={summarize_facebook_batch_status(results)}"
        )
        return results

    results: list[ChannelResult] = []
    state = load_state()
    for ch in wanted:
        if is_channel_done(state, job.object_id, ch):
            results.append(
                ChannelResult(
                    channel=ch,
                    ok=True,
                    skipped=True,
                    reason="already done (local state)",
                    publication_status="skipped",
                )
            )
            continue
        results.append(
            _execute_channel(
                job,
                ch,
                dry_run=dry_run,
                confirm_post=confirm_post,
                cfg=cfg,
                android_cfg=android_cfg,
                state=state,
            )
        )
    return results


def _maybe_schedule_delayed_carousel(
    state: dict[str, Any],
    job: PublishJob,
    published_channel: str,
    config: dict[str, Any],
) -> None:
    plan = _orchestration_plan(config)
    for carousel_ch, spec in plan["delayed"].items():
        if spec.get("after") != published_channel:
            continue
        if is_channel_done(state, job.object_id, carousel_ch):
            continue
        delay = int(spec.get("delay_minutes") or 60)
        publish_after = (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat()
        schedule_channel(
            state,
            object_id=job.object_id,
            page_id=job.page_id,
            channel=carousel_ch,
            publish_after=publish_after,
            after_channel=published_channel,
        )
        append_log(
            state,
            job.object_id,
            f"scheduled {carousel_ch} at {publish_after} (+{delay}m after {published_channel})",
        )


def run_channels_with_retry(
    job: PublishJob,
    *,
    channels: list[str],
    dry_run: bool = True,
    push_media: bool = False,
    confirm_post: bool = False,
    max_attempts: int = 3,
) -> list[ChannelResult]:
    """Прогон каналов с повторами до успеха (для --live)."""
    import time

    cfg = load_publisher_config()
    state = load_state()
    android_cfg = load_android_config()
    all_results: list[ChannelResult] = []
    channels = [
        channel
        for channel in channels
        if not is_channel_done(state, job.object_id, channel)
    ] or list(channels)

    if facebook_batch_applies(cfg, channels):
        return run_channels(
            job,
            channels=channels,
            dry_run=dry_run,
            push_media=push_media,
            confirm_post=confirm_post,
        )

    if not (cfg.get("orchestration") or {}).get("automatic_live_retries", False):
        max_attempts = 1 if confirm_post and not dry_run else max_attempts

    if not dry_run and (push_media or confirm_post):
        status = check_adb(android_cfg)
        if not status.get("ok"):
            raise RuntimeError(status.get("error") or "adb not ready")
        need_mp = any(ch == "fb_marketplace" for ch in channels)
        job = prepare_job(
            job, push_to_device=True, android_cfg=android_cfg, push_marketplace=need_mp
        )

    if not dry_run:
        from .android.ui import connect_device, ensure_unlocked

        try:
            ensure_unlocked(connect_device(android_cfg))
        except Exception:
            pass

    for ch in channels:
        current_state = load_state()
        if is_channel_done(current_state, job.object_id, ch):
            all_results.append(
                ChannelResult(
                    channel=ch,
                    ok=True,
                    skipped=True,
                    reason="already done (local state); retry suppressed",
                    publication_status="skipped",
                )
            )
            continue
        inflight = channel_inflight(current_state, job.object_id, ch)
        if inflight:
            all_results.append(
                ChannelResult(
                    channel=ch,
                    ok=False,
                    skipped=True,
                    reason="manual_verification_required; retry suppressed",
                    publication_status="submitted_unverified",
                )
            )
            continue

        last: ChannelResult | None = None
        for attempt in range(1, max_attempts + 1):
            reset_uiautomator(android_cfg)
            status = check_adb(android_cfg)
            if not status.get("ok"):
                last = ChannelResult(
                    channel=ch,
                    ok=False,
                    reason=status.get("error") or "adb not ready",
                )
                all_results.append(last)
                append_log(
                    state,
                    job.object_id,
                    f"retry {ch} attempt {attempt}/{max_attempts}: {last.reason}",
                )
                time.sleep(15)
                continue

            batch = run_channels(
                job,
                channels=[ch],
                dry_run=dry_run,
                push_media=False,
                confirm_post=confirm_post,
            )
            last = batch[0] if batch else None
            if not last:
                break
            all_results.append(last)
            if last.ok and not last.skipped:
                break
            if last.skipped:
                append_log(
                    state,
                    job.object_id,
                    f"retry suppressed for {ch}: {last.reason or last.note}",
                )
                break
            append_log(
                state,
                job.object_id,
                f"retry {ch} attempt {attempt}/{max_attempts}: {last.reason or last.note}",
            )
            if attempt < max_attempts and confirm_post and not dry_run:
                time.sleep(10)
        if last and not (last.ok and not last.skipped):
            append_log(state, job.object_id, f"FAILED after {max_attempts} attempts: {ch}")
    return all_results


def run_publish_all(
    job: PublishJob,
    *,
    dry_run: bool = True,
    push_media: bool = False,
    confirm_post: bool = False,
) -> list[ChannelResult]:
    cfg = load_publisher_config()
    plan = _orchestration_plan(cfg)
    # video → carousel → final (fb_marketplace последним)
    immediate = plan["video"] + plan["carousel"] + plan["final"]
    # на всякий случай: marketplace не раньше конца списка
    ordered = [c for c in immediate if c != "fb_marketplace"]
    if "fb_marketplace" in immediate:
        ordered.append("fb_marketplace")
    return run_channels(
        job,
        channels=ordered,
        dry_run=dry_run,
        push_media=push_media,
        confirm_post=confirm_post,
    )


def run_scheduled(
    *,
    dry_run: bool = True,
    push_media: bool = False,
    confirm_post: bool = False,
    page_id: str | None = None,
    object_id: str | None = None,
) -> list[tuple[PublishJob, ChannelResult]]:
    cfg = load_publisher_config()
    state = load_state()
    results: list[tuple[PublishJob, ChannelResult]] = []
    for entry in due_scheduled(state):
        if page_id is not None and entry.get("page_id") != page_id:
            continue
        if object_id is not None and entry.get("object_id") != object_id:
            continue
        channel_name = entry["channel"]
        if is_channel_done(load_state(), entry["object_id"], channel_name):
            result = ChannelResult(
                channel=channel_name,
                ok=True,
                skipped=True,
                reason="scheduled entry consumed: already done (local state)",
                publication_status="skipped",
            )
            if not dry_run:
                clear_scheduled(
                    state,
                    object_id=entry["object_id"],
                    channel=channel_name,
                )
            results.append(
                (
                    PublishJob(
                        page_id=entry["page_id"],
                        object_id=entry["object_id"],
                        title="",
                        caption_social="",
                        caption_fb="",
                    ),
                    result,
                )
            )
            continue

        page = notion.get_page(entry["page_id"])
        job = build_job_from_page(page, config=cfg, channels=[channel_name])
        if channel_name not in job.channels_pending:
            result = ChannelResult(
                channel=channel_name,
                ok=True,
                skipped=True,
                reason="scheduled entry consumed: already published in Notion",
                publication_status="skipped",
            )
            if not dry_run:
                clear_scheduled(
                    state,
                    object_id=job.object_id,
                    channel=channel_name,
                )
            results.append((job, result))
            continue

        channel_results = run_channels(
            job,
            channels=[channel_name],
            dry_run=dry_run,
            push_media=push_media,
            confirm_post=confirm_post,
        )
        for result in channel_results:
            results.append((job, result))
            if result.ok and not result.skipped and not dry_run:
                clear_scheduled(state, object_id=job.object_id, channel=channel_name)
    return results


def retry_pending_notion_updates(
    *,
    only_keys: set[str] | None = None,
) -> tuple[int, int]:
    """Deliver durable Notion writes without ever republishing a social post."""
    state = load_state()
    delivered = 0
    failed = 0
    for entry in pending_notion_updates(state):
        key = str(entry.get("key") or "")
        if only_keys is not None and key not in only_keys:
            continue
        try:
            if entry.get("channel") == "_published_at":
                field_name = next(iter(entry["properties"]), "")
                page = notion.get_page(entry["page_id"])
                if field_name and notion.get_prop(page, field_name, "date"):
                    clear_notion_update(state, key)
                    delivered += 1
                    continue
            notion.update_fields(entry["page_id"], entry["properties"])
        except Exception as exc:  # noqa: BLE001
            failed += 1
            mark_notion_update_failed(state, key, str(exc))
            append_log(
                state,
                entry.get("object_id") or entry["page_id"],
                f"notion outbox failed for {entry.get('channel')}: {exc}",
            )
            continue

        clear_notion_update(state, key)
        delivered += 1
    return delivered, failed


def _try_write_notion_result(job: PublishJob, channel: str, result: ChannelResult) -> None:
    """Persist URL update to an outbox first; delivery can safely be retried."""
    cfg = load_publisher_config()
    fields = notion_fields(cfg)
    updates: dict[str, Any] = {}

    url_field = _channel_url_field(channel, fields)
    if (
        result.publication_status == "verified"
        and _is_real_post_url(result.post_url, channel)
        and url_field
    ):
        if channel == "fb_groups":
            updates[url_field] = notion.rich_text_prop(result.post_url)
        else:
            updates[url_field] = notion.url_prop(result.post_url)

    for key, url in (result.extra_urls or {}).items():
        field_name = fields.get(key)
        if field_name and url:
            updates[field_name] = notion.url_prop(url)

    if not updates:
        return
    state = load_state()
    key = f"{job.page_id}:{channel}"
    enqueue_notion_update(
        state,
        key=key,
        page_id=job.page_id,
        object_id=job.object_id,
        channel=channel,
        properties=updates,
    )
    retry_pending_notion_updates(only_keys={key})


def record_captured_post_url(
    page: dict[str, Any],
    channel: str,
    url: str,
    extra_urls: dict[str, str] | None = None,
) -> None:
    """Upgrade an unverified local submission and durably sync its URL to Notion."""
    if not _is_real_post_url(url, channel):
        raise ValueError(f"captured URL does not match channel {channel}")
    cfg = load_publisher_config()
    fields = notion_fields(cfg)
    object_id = (
        notion.get_prop(page, fields.get("object_id") or "Объект ID", "rich_text")
        or page["id"]
    )
    mark_channel_verified(
        load_state(),
        object_id,
        channel,
        post_url=url,
    )
    job = PublishJob(
        page_id=page["id"],
        object_id=object_id,
        title="",
        caption_social="",
        caption_fb="",
    )
    _try_write_notion_result(
        job,
        channel,
        ChannelResult(
            channel=channel,
            ok=True,
            post_url=url,
            extra_urls=extra_urls,
            publication_status="verified",
        ),
    )


def invalidate_captured_post_url(
    page: dict[str, Any],
    channel: str,
    *,
    reason: str,
) -> None:
    """Удалить неверный URL из Notion, не разрешая повторную публикацию канала."""
    cfg = load_publisher_config()
    fields = notion_fields(cfg)
    object_id = (
        notion.get_prop(page, fields.get("object_id") or "Объект ID", "rich_text")
        or page["id"]
    )
    invalidate_channel_post_url(
        load_state(),
        object_id,
        channel,
        note=reason,
    )
    url_field = _channel_url_field(channel, fields)
    if not url_field:
        return
    key = f"{page['id']}:{channel}:invalidate-url"
    enqueue_notion_update(
        load_state(),
        key=key,
        page_id=page["id"],
        object_id=object_id,
        channel=channel,
        properties={url_field: notion.url_prop(None)},
    )
    retry_pending_notion_updates(only_keys={key})


def run_publish_chain(
    page_id: str,
    *,
    dry_run: bool = True,
    push_media: bool = False,
    confirm_post: bool = False,
    reset_channels: list[str] | None = None,
    max_attempts: int = 2,
) -> int:
    """Один объект: отложенные карусели → все pending-каналы по очереди (с lock на телефон)."""
    cfg = load_publisher_config()
    page = notion.get_page(page_id)
    fields = notion_fields(cfg)
    object_id = notion.get_prop(page, fields["object_id"], "rich_text") or page_id
    initial_job = build_job_from_page(page, config=cfg)

    state = load_state()
    if reset_channels:
        for ch in reset_channels:
            clear_channel_done(state, object_id, ch)
            append_log(state, object_id, f"chain reset channel: {ch}")

    exit_code = 0

    if not dry_run:
        reconciled = reconcile_local_completed_from_notion(page, initial_job, cfg)
        if reconciled:
            print(f"Reconciled verified from Notion: {','.join(reconciled)}")
        recovered = reconcile_missing_delayed_schedules(page, initial_job, cfg)
        if recovered:
            print(f"Recovered due scheduled: {','.join(recovered)}")

    if confirm_post and not dry_run:
        from .android.ui import connect_device, ensure_unlocked

        d = connect_device(load_android_config())
        ensure_unlocked(d)
        if "keyguard" in (d.dump_hierarchy() or "").lower():
            print("ОШИБКА: телефон заблокирован — разблокируйте экран и запустите снова.", file=sys.stderr)
            return 2

    print("=== publish-chain: scheduled carousels ===")
    scheduled_results = run_scheduled(
        dry_run=dry_run,
        push_media=push_media,
        confirm_post=confirm_post,
        page_id=page_id,
    )
    for job, result in scheduled_results:
        flag = (
            "WARN"
            if result.publication_status == "submitted_unverified"
            else ("OK" if result.ok else "FAIL")
        )
        skip = " skip" if result.skipped else ""
        url = f" url={result.post_url}" if result.post_url else ""
        print(
            f"[{flag}{skip}] {job.object_id} {result.channel}: "
            f"{result.reason or result.note or ''}{url}"
        )
    if scheduled_results:
        result = scheduled_results[-1][1]
        return 1 if not result.ok and not result.skipped else 0

    page = notion.get_page(page_id)
    job = build_job_from_page(page, config=cfg)
    pending = immediate_channels_for_queue(
        job,
        cfg,
        requested=list(reset_channels) if reset_channels else None,
    )
    if not pending:
        print("Нет pending-каналов — всё опубликовано или пропущено.")
        return 0

    print(summarize_job(job))
    print(f"=== publish-chain: pending {','.join(pending)} ===")
    results = run_channels_with_retry(
        job,
        channels=pending,
        dry_run=dry_run,
        push_media=push_media,
        confirm_post=confirm_post,
        max_attempts=max_attempts,
    )
    for last in results:
        flag = (
            "WARN"
            if last.publication_status == "submitted_unverified"
            else ("OK" if last.ok and not last.skipped else "FAIL")
        )
        skip = " skip" if last.skipped else ""
        url = f" url={last.post_url}" if last.post_url else ""
        print(f"[{flag}{skip}] {last.channel}: {last.reason or last.note or ''}{url}")
        if not last.ok and not last.skipped:
            exit_code = 1

    return exit_code


def summarize_job(job: PublishJob) -> str:
    lines = [
        f"object_id={job.object_id}",
        f"title={job.title}",
        f"page_id={job.page_id}",
        f"video={'yes' if job.video_url else 'no'}",
        f"images={len(job.image_urls)} (source={job.images_source})",
        f"marketplace_images={len(job.marketplace_image_urls)} (brand_open_home)",
        f"caption_social_len={len(job.caption_social)}",
        f"caption_fb_len={len(job.caption_fb)}",
        f"pending={','.join(job.channels_pending) or '-'}",
        f"fb_groups={len(job.fb_groups)}",
    ]
    return "\n".join(lines)
