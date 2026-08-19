from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from ..models import PublishJob

# Карусели из Notion carousel_url: slide_01.jpg — ценовой хук (build_carousel.mjs).
# На телефоне галерея показывает новые файлы сверху; выбор всегда с slide_01.
CAROUSEL_PUBLISH_CHANNELS = (
    "tiktok_carousel",
    "instagram_carousel",
    "linkedin",
    "twitter",
    "fb_groups",
)

# Android album folder names — one directory per object (see tests/test_media_selection.py).
CAROUSEL_FOLDER_PREFIX = "Carousel"
MARKETPLACE_FOLDER_PREFIX = "Open Home"


def carousel_folder_name(object_id: str) -> str:
    """Folder under publisher_social media_dir for carousel slides of one object."""
    oid = (object_id or "").strip()
    if not oid:
        raise ValueError("object_id required for carousel folder name")
    return f"{CAROUSEL_FOLDER_PREFIX} {oid}"


def marketplace_folder_name(object_id: str) -> str:
    """Folder under brand_open_home device dir for Marketplace photos of one object."""
    oid = (object_id or "").strip()
    if not oid:
        raise ValueError("object_id required for marketplace folder name")
    return f"{MARKETPLACE_FOLDER_PREFIX} {oid}"


@dataclass
class ChannelResult:
    channel: str
    ok: bool
    skipped: bool = False
    reason: str | None = None
    post_url: str | None = None
    extra_urls: dict[str, str] | None = None
    note: str | None = None
    # FB Groups: URLs successfully published in this run (for per-group Notion matrix).
    published_group_urls: list[str] | None = None
    # verified | accepted | submitted_unverified | failed | skipped | preview
    publication_status: str | None = None


def require_designed_carousel(
    job: PublishJob, channel: str, *, min_images: int = 2
) -> ChannelResult | None:
    """Карусели только из Notion carousel_url (слайды с шаблоном)."""
    n = len(job.device_images or job.local_images or job.image_urls)
    if job.images_source == "carousel_url" and n >= min_images:
        return None
    if job.images_source != "carousel_url":
        return ChannelResult(
            channel=channel,
            ok=False,
            skipped=True,
            reason="need Notion carousel_url (designed slides), raw «Фото» disabled",
        )
    return ChannelResult(
        channel=channel,
        ok=False,
        skipped=True,
        reason=f"need >= {min_images} carousel slides, got {n}",
    )


def device_media_album(paths: list[str], fallback: str) -> str:
    """Имя Android-альбома из фактического каталога pushed media."""
    if paths:
        parent = PurePosixPath(paths[0]).parent.name
        if parent:
            return parent
    return fallback


def carousel_grid_pick_order(
    cells: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """
    Re-order gallery grid cells so slide_01 (price-hook template) is tapped first.

    Slides are pushed as slide_01..slide_N; Android galleries list newest files
    top-left, so slide_01 is usually bottom-right. Cells are (sort_y, sort_x, tap_x, tap_y).
    """
    ordered = sorted(cells)
    ordered.reverse()
    return ordered


def carousel_bounds_pick_order(
    bounds: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Same as carousel_grid_pick_order for raw (x1, y1, x2, y2) thumbnail bounds."""
    ordered = sorted(bounds, key=lambda b: (b[1], b[0]))
    ordered.reverse()
    return ordered


def bounds_center(bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bounds
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    return y1, x1, cx, cy


def select_carousel_photos_toggle_safe(
    d,
    *,
    max_images: int,
    collect_unselected,
    pause_s: float = 0.45,
    min_selected: int = 1,
) -> int:
    """
    Select carousel slides without toggling off already-selected thumbnails.
    collect_unselected(xml) must return cells ordered slide_01-first.
    """
    max_images = max(1, int(max_images))
    attempts = 0
    limit = max_images * 4
    while attempts < limit:
        xml = d.dump_hierarchy() or ""
        if count_selected_gallery_photos(xml) >= max_images:
            break
        photos = collect_unselected(xml)
        if not photos:
            break
        _, __, cx, cy = photos[0]
        d.click(cx, cy)
        time.sleep(pause_s)
        attempts += 1
    selected = count_selected_gallery_photos(d.dump_hierarchy() or "")
    if selected < min_selected:
        raise RuntimeError(
            f"carousel: выбрано {selected} фото, нужно минимум {min_selected}"
        )
    return min(selected, max_images)


def gallery_selection_state(desc: str) -> str | None:
    """Parse Android gallery content-desc into unselected/selected."""
    low = (desc or "").lower()
    if "не выбрано" in low or "not selected" in low or "не выбран" in low:
        return "unselected"
    if (
        "выбрано" in low
        or " selected" in low
        or low.endswith("selected")
        or "отмечено" in low
        or "checked" in low
    ):
        return "selected"
    return None


def count_selected_gallery_photos(xml: str) -> int:
    """Count thumbnails marked selected in a uiautomator hierarchy dump."""
    from xml.etree import ElementTree as ET

    count = 0
    for node in ET.fromstring(xml).iter("node"):
        desc = node.attrib.get("content-desc") or ""
        low = desc.lower()
        if "сделать" in low or "camera" in low:
            continue
        if (
            "фото" not in low
            and "photo" not in low
            and "дата и время" not in low
            and "photo taken on" not in low
        ):
            continue
        if gallery_selection_state(desc) == "selected":
            count += 1
    return count


class Channel(Protocol):
    name: str

    def publish(
        self,
        job: PublishJob,
        *,
        dry_run: bool,
        android_cfg: dict[str, Any],
        publisher_cfg: dict[str, Any],
        confirm_post: bool = False,
    ) -> ChannelResult: ...


def list_channels() -> list[str]:
    from . import fb_groups, fb_marketplace, instagram, linkedin, tiktok, twitter, youtube

    return [
        tiktok.TikTokChannel.name,
        tiktok.TikTokCarouselChannel.name,
        instagram.InstagramReelChannel.name,
        instagram.InstagramCarouselChannel.name,
        youtube.YoutubeShortsChannel.name,
        linkedin.LinkedInChannel.name,
        twitter.TwitterChannel.name,
        fb_groups.FbGroupsChannel.name,
        fb_marketplace.FbMarketplaceChannel.name,
    ]


def get_channel(name: str) -> Channel:
    from . import fb_groups, fb_marketplace, instagram, linkedin, tiktok, twitter, youtube

    mapping: dict[str, Channel] = {
        tiktok.TikTokChannel.name: tiktok.TikTokChannel(),
        tiktok.TikTokCarouselChannel.name: tiktok.TikTokCarouselChannel(),
        instagram.InstagramReelChannel.name: instagram.InstagramReelChannel(),
        instagram.InstagramCarouselChannel.name: instagram.InstagramCarouselChannel(),
        fb_marketplace.FbMarketplaceChannel.name: fb_marketplace.FbMarketplaceChannel(),
        fb_groups.FbGroupsChannel.name: fb_groups.FbGroupsChannel(),
        linkedin.LinkedInChannel.name: linkedin.LinkedInChannel(),
        twitter.TwitterChannel.name: twitter.TwitterChannel(),
        youtube.YoutubeShortsChannel.name: youtube.YoutubeShortsChannel(),
    }
    if name not in mapping:
        raise ValueError(f"Unknown channel: {name}. Known: {', '.join(mapping)}")
    return mapping[name]
