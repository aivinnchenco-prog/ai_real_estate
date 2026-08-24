from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

from ..channels.base import ChannelResult
from ..models import PublishJob
from ..state import load_state, mark_fb_group_published
from .session import (
    agent4_groups_cfg,
    agent4_marketplace_cfg,
    browser_context,
    ensure_logged_in,
    import_agent4_groups,
    import_agent4_marketplace,
)


def _image_paths(job: PublishJob, *, marketplace: bool) -> list[Path]:
    if marketplace:
        raw = job.local_marketplace_images or job.marketplace_image_urls
    else:
        raw = job.local_images or job.image_urls
    paths: list[Path] = []
    for item in raw:
        p = Path(item)
        if p.is_file():
            paths.append(p)
    return paths


def _housing_to_property_type(housing: str | None, cfg: dict[str, Any]) -> str:
    mapping = (cfg.get("listing") or {}).get("property_type_by_housing") or {}
    if housing and housing in mapping:
        return mapping[housing]
    low = (housing or "").strip().lower()
    for key, value in mapping.items():
        if key.lower() in low or low in key.lower():
            return value
    return mapping.get("default", "house")


def _location_queries(job: PublishJob, publisher_cfg: dict[str, Any]) -> list[str]:
    mp = publisher_cfg.get("fb_marketplace") or {}
    listing = job.listing
    queries: list[str] = []
    fallback = (mp.get("address_fallback") or "Amphoe Thalang").strip()
    if listing.district:
        queries.append(f"{listing.district.strip()}, Phuket")
    if mp.get("address_search"):
        queries.append(str(mp["address_search"]))
    if listing.address:
        queries.append(f"{listing.address.strip()}, Phuket, Thailand")
    queries.extend(
        [
            f"{fallback}, Phuket, Thailand",
            f"{fallback}, Thailand",
            "Thalang Phuket",
            "Phuket Thalang",
        ]
    )
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _format_rooms(value: float | int | None) -> str:
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return str(value).rstrip("0").rstrip(".")


def _listing_dict(job: PublishJob, publisher_cfg: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    listing = job.listing
    caption = (job.caption_fb or job.caption_social or "").strip()
    if not caption:
        raise ValueError("пустой caption_fb/caption_social")
    if listing.price_monthly is None or listing.rooms is None or listing.bathrooms is None:
        raise ValueError("нужны price_monthly, rooms, bathrooms для Marketplace")
    queries = _location_queries(job, publisher_cfg)
    if not queries:
        raise ValueError("не удалось собрать location_queries для Marketplace")
    beds = _format_rooms(listing.rooms)
    baths = _format_rooms(listing.bathrooms)
    return {
        "description": caption,
        "price": int(round(float(listing.price_monthly))),
        "bedrooms": int(float(beds)) if beds else int(listing.rooms),
        "bathrooms": int(float(baths)) if baths else int(listing.bathrooms),
        "property_type": _housing_to_property_type(listing.housing_type, cfg),
        "housing_type": listing.housing_type or "",
        "area_sqm": None,
        "location_queries": queries,
    }


def _pause_between_groups(cfg: dict[str, Any]) -> None:
    lo, hi = cfg.get("limits", {}).get("minutes_between_groups", [2, 5])
    minutes = random.uniform(float(lo), float(hi))
    print(f"[fb_groups] pause {minutes:.1f} min before next group", flush=True)
    end = time.time() + minutes * 60
    next_log = time.time() + 30
    while time.time() < end:
        if time.time() >= next_log:
            print(f"[fb_groups] pause… {int(end - time.time())}s left", flush=True)
            next_log += 30
        time.sleep(5)


def post_group_without_submit(
    page: Any,
    group_url: str,
    caption: str,
    files: list[Path],
    cfg: dict[str, Any],
    fgg: Any,
) -> dict[str, Any]:
    """UI-safe: composer filled, «Опубликовать» не нажата."""
    nav_timeout = int(cfg.get("browser", {}).get("nav_timeout_ms", 90000))
    page.goto(group_url, wait_until="domcontentloaded", timeout=nav_timeout)
    fgg.human_delay(cfg, 2.0)
    fgg.idle_scroll(page, cfg, (5, 12))
    page.keyboard.press("Home")
    fgg.human_delay(cfg)
    fgg.maybe_join_group(page, cfg)
    fgg.close_blocking_dialogs(page, cfg)
    dialog = fgg.open_composer(page, cfg)
    fgg.human_delay(cfg)
    textbox = dialog.locator('div[role="textbox"][contenteditable="true"]').first
    textbox.click()
    fgg.human_delay(cfg)
    fgg.type_like_human(page, caption, cfg)
    fgg.human_delay(cfg)
    if files:
        fgg.attach_photos(page, dialog, files, cfg)
        fgg.human_delay(cfg, 1.5)
    return {"post_url": None, "pending_approval": False, "stopped_before_publish": True}


def publish_fb_groups_browser(
    job: PublishJob,
    *,
    publisher_cfg: dict[str, Any],
    confirm_post: bool,
) -> ChannelResult:
    images = _image_paths(job, marketplace=False)
    groups = job.fb_groups
    max_live = int(os.getenv("PUBLISHER_FB_MAX_GROUPS", "0") or 0)
    if max_live > 0:
        groups = groups[:max_live]
    caption = (job.caption_fb or job.caption_social or "").strip()
    if not groups:
        return ChannelResult(
            channel="fb_groups",
            ok=True,
            skipped=True,
            reason="all groups already published for this object",
        )
    if not images and not caption:
        return ChannelResult(
            channel="fb_groups",
            ok=False,
            skipped=True,
            reason="no images/caption",
        )

    cfg = agent4_groups_cfg(publisher_cfg)
    fgg = import_agent4_groups()
    files = [Path(p) for p in images]
    note = (
        f"FB Groups browser: targets={len(groups)}; images={len(files)}; "
        f"caption_len={len(caption)}; profile=agent7"
    )
    published_urls: list[str] = []
    skipped_groups: list[str] = []
    last_url: str | None = None
    state = load_state()

    def _skip_group(url: str, reason: str) -> None:
        skipped_groups.append(url)
        note_parts.append(f"skipped={url} ({reason})")

    note_parts = [note]

    try:
        with browser_context() as context:
            page = context.pages[0] if context.pages else context.new_page()
            ensure_logged_in(page, context)
            fgg.idle_scroll(page, cfg, (8, 20))

            for index, group_url in enumerate(groups):
                print(
                    f"[fb_groups] {index + 1}/{len(groups)}: {group_url}",
                    flush=True,
                )
                if index > 0 and confirm_post:
                    _pause_between_groups(cfg)
                try:
                    if confirm_post:
                        out = fgg.post_to_group(page, group_url, caption, files, cfg)
                    else:
                        out = post_group_without_submit(
                            page, group_url, caption, files, cfg, fgg
                        )
                except RuntimeError as exc:
                    msg = str(exc)
                    low = msg.lower()
                    if any(
                        token in low
                        for token in (
                            "не участник",
                            "not member",
                            "composer_not_found",
                            "post_button_disabled",
                            "composer_no_textbox",
                            "upload_timeout",
                        )
                    ):
                        _skip_group(group_url, msg)
                        continue
                    raise
                post_url = out.get("post_url")
                if out.get("stopped_before_publish"):
                    return ChannelResult(
                        channel="fb_groups",
                        ok=True,
                        skipped=True,
                        reason="stopped_before_publish",
                        note=f"{'; '.join(note_parts)}; group={group_url}",
                    )
                if confirm_post:
                    mark_fb_group_published(
                        state,
                        job.object_id,
                        group_url,
                        note=f"browser post_url={post_url}",
                    )
                    published_urls.append(group_url)
                    if post_url:
                        last_url = post_url

        if not published_urls and skipped_groups:
            return ChannelResult(
                channel="fb_groups",
                ok=False,
                reason="all_targets_skipped",
                note="; ".join(note_parts),
                published_group_urls=None,
            )
        if skipped_groups:
            note_parts.append(f"skipped_not_member={skipped_groups}")
        return ChannelResult(
            channel="fb_groups",
            ok=bool(published_urls),
            post_url=last_url,
            note="; ".join(note_parts),
            published_group_urls=published_urls or None,
        )
    except Exception as exc:
        return ChannelResult(
            channel="fb_groups",
            ok=False,
            reason=str(exc),
            note=note,
            published_group_urls=published_urls or None,
        )


def publish_fb_marketplace_browser(
    job: PublishJob,
    *,
    publisher_cfg: dict[str, Any],
    confirm_post: bool,
) -> ChannelResult:
    images = _image_paths(job, marketplace=True)
    if not images:
        return ChannelResult(
            channel="fb_marketplace",
            ok=False,
            skipped=True,
            reason="no marketplace images (brand_open_home_url)",
        )

    cfg = agent4_marketplace_cfg(publisher_cfg)
    fmp = import_agent4_marketplace()
    fgg = import_agent4_groups()
    files = [Path(p) for p in images]
    try:
        listing = _listing_dict(job, publisher_cfg, cfg)
    except ValueError as exc:
        return ChannelResult(
            channel="fb_marketplace",
            ok=False,
            skipped=True,
            reason=str(exc),
        )

    note = (
        f"FB MP browser: brand_images={len(files)}; price={listing['price']}; "
        f"beds={listing['bedrooms']}; profile=agent7"
    )

    try:
        with browser_context() as context:
            page = context.pages[0] if context.pages else context.new_page()
            ensure_logged_in(page, context)
            fgg.idle_scroll(page, cfg, (8, 20))

            if not confirm_post:
                nav_timeout = int(cfg.get("browser", {}).get("nav_timeout_ms", 90000))
                page.goto(cfg["create_url"], wait_until="domcontentloaded", timeout=nav_timeout)
                fgg.human_delay(cfg, 2.0)
                if "/marketplace/create" not in page.url:
                    raise RuntimeError(f"MP_FORM_UNAVAILABLE: redirect to {page.url}")
                fmp.attach_photos(page, files[: int(cfg.get("media", {}).get("max_images", 8))], cfg)
                return ChannelResult(
                    channel="fb_marketplace",
                    ok=True,
                    skipped=True,
                    reason="stopped_before_publish",
                    note=f"{note}; form opened, photos attached (--live for publish)",
                )

            out = fmp.create_listing(page, listing, files, cfg)
            post_url = out.get("post_url")
            return ChannelResult(
                channel="fb_marketplace",
                ok=True,
                post_url=post_url,
                note=f"{note}; listing={out}",
            )
    except Exception as exc:
        return ChannelResult(
            channel="fb_marketplace",
            ok=False,
            reason=str(exc),
            note=note,
        )
