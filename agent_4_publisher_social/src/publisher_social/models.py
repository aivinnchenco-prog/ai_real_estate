from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


CHANNELS = (
    "fb_groups",
    "fb_marketplace",
)


@dataclass
class ListingFields:
    title: str = ""
    housing_type: str | None = None
    rooms: float | None = None
    bathrooms: float | None = None
    price_monthly: float | None = None
    district: str | None = None
    address: str | None = None
    google_maps: str | None = None


@dataclass
class PublishJob:
    """Один объект из Notion, готовый к публикации с телефона."""

    page_id: str
    object_id: str
    title: str
    caption_social: str
    caption_fb: str
    caption_x: str = ""
    cta_instagram: str = ""
    title_youtube_shorts: str = ""
    caption_youtube_shorts: str = ""
    video_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    # Откуда image_urls: carousel_url | photo_raw | none
    images_source: str = "none"
    # Только FB Marketplace: картинки из Notion brand_open_home_url
    marketplace_image_urls: list[str] = field(default_factory=list)
    listing: ListingFields = field(default_factory=ListingFields)
    fb_groups: list[str] = field(default_factory=list)
    channels_pending: list[str] = field(default_factory=list)
    local_video: str | None = None
    local_images: list[str] = field(default_factory=list)
    local_marketplace_images: list[str] = field(default_factory=list)
    device_video: str | None = None
    device_images: list[str] = field(default_factory=list)
    device_marketplace_images: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublishJob:
        listing_raw = data.get("listing") or {}
        listing = ListingFields(**{
            k: listing_raw.get(k)
            for k in ListingFields.__dataclass_fields__
        })
        return cls(
            page_id=data["page_id"],
            object_id=data["object_id"],
            title=data.get("title") or "",
            caption_social=data.get("caption_social") or "",
            caption_x=data.get("caption_x") or "",
            caption_fb=data.get("caption_fb") or "",
            cta_instagram=data.get("cta_instagram") or "",
            title_youtube_shorts=data.get("title_youtube_shorts") or "",
            caption_youtube_shorts=data.get("caption_youtube_shorts") or "",
            video_url=data.get("video_url"),
            image_urls=list(data.get("image_urls") or []),
            images_source=str(data.get("images_source") or "none"),
            marketplace_image_urls=list(data.get("marketplace_image_urls") or []),
            listing=listing,
            fb_groups=list(data.get("fb_groups") or []),
            channels_pending=list(data.get("channels_pending") or []),
            local_video=data.get("local_video"),
            local_images=list(data.get("local_images") or []),
            local_marketplace_images=list(data.get("local_marketplace_images") or []),
            device_video=data.get("device_video"),
            device_images=list(data.get("device_images") or []),
            device_marketplace_images=list(
                data.get("device_marketplace_images") or []
            ),
        )
