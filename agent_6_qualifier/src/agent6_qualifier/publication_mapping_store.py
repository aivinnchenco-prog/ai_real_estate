"""Publication URL / ID → listing mapping (Notion-backed)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .publication_url_normalize import NormalizedPublicationUrl, normalize_publication_url


# Notion column names (Agent 4 published_url_fields + phone publisher).
PUBLICATION_URL_FIELDS: dict[str, str] = {
    "instagram_carousel": "post_url_instagram_carousel",
    "instagram_reel": "post_url_instagram_reel",
    "instagram": "post_url_instagram_carousel",
    "tiktok": "post_url_tiktok",
    "x": "post_url_x",
    "twitter": "post_url_x",
    "linkedin": "post_url_linkedin",
    "facebook": "post_url_facebook",
    "fb_marketplace": "post_url_facebook",
    "youtube": "post_url_youtube",
    "threads": "post_url_threads",
    "telegram": "post_url_telegram",
    "fb_groups": "post_url_fb_groups",
}

PUBLICATION_ID_FIELDS: dict[str, str] = {
    "postmypost": "metricool_post_id",
    "metricool": "metricool_post_id",
}


@dataclass(frozen=True)
class PublicationMapping:
    listing_id: str
    page_id: str
    platform: str
    publication_id: str | None
    canonical_url: str
    notion_url: str


class PublicationMappingStore(Protocol):
    def find_by_url(self, canonical_url: str, platform: str | None = None) -> PublicationMapping | None: ...

    def find_by_publication_id(
        self, platform: str, publication_id: str
    ) -> PublicationMapping | None: ...

    def find_by_platform_reference(
        self, platform: str, publication_ref: str
    ) -> PublicationMapping | None: ...


def _plain_url(value: str) -> str:
    return (value or "").strip()


def _split_fb_groups_urls(raw: str) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip().startswith("http")]


class InMemoryPublicationMappingStore:
    """Offline/test store."""

    def __init__(self, mappings: list[PublicationMapping] | None = None) -> None:
        self._by_url: dict[str, PublicationMapping] = {}
        self._by_pub_id: dict[tuple[str, str], PublicationMapping] = {}
        self._by_ref: dict[tuple[str, str], PublicationMapping] = {}
        for item in mappings or []:
            self._index(item)

    def _index(self, item: PublicationMapping) -> None:
        key = (item.platform, item.canonical_url)
        self._by_url[item.canonical_url] = item
        if item.publication_id:
            self._by_pub_id[(item.platform, item.publication_id)] = item
        norm = normalize_publication_url(item.canonical_url)
        if norm and norm.publication_ref:
            self._by_ref[(item.platform, norm.publication_ref)] = item

    def find_by_url(self, canonical_url: str, platform: str | None = None) -> PublicationMapping | None:
        hit = self._by_url.get(canonical_url)
        if hit and (not platform or hit.platform == platform):
            return hit
        return None

    def find_by_publication_id(
        self, platform: str, publication_id: str
    ) -> PublicationMapping | None:
        return self._by_pub_id.get((platform, publication_id))

    def find_by_platform_reference(
        self, platform: str, publication_ref: str
    ) -> PublicationMapping | None:
        return self._by_ref.get((platform, publication_ref))


class NotionPublicationMappingStore:
    """Builds mapping index from Notion pages (existing post_url_* columns)."""

    def __init__(self, fetch_pages, *, object_id_prop: str = "Объект ID") -> None:
        self._fetch_pages = fetch_pages
        self._object_id_prop = object_id_prop
        self._index: list[PublicationMapping] | None = None

    def _ensure_index(self) -> list[PublicationMapping]:
        if self._index is not None:
            return self._index
        mappings: list[PublicationMapping] = []
        for page in self._fetch_pages():
            props = page.get("properties", {})
            listing_id = _plain_prop(props.get(self._object_id_prop))
            page_id = page.get("id", "")
            if not listing_id:
                continue
            for platform, field in PUBLICATION_URL_FIELDS.items():
                raw = _plain_prop(props.get(field))
                if not raw:
                    continue
                urls = _split_fb_groups_urls(raw) if platform == "fb_groups" else [raw]
                for url in urls:
                    norm = normalize_publication_url(url)
                    if not norm:
                        continue
                    mappings.append(
                        PublicationMapping(
                            listing_id=listing_id,
                            page_id=page_id,
                            platform=platform if platform != "instagram" else norm.platform,
                            publication_id=None,
                            canonical_url=norm.canonical_url,
                            notion_url=url,
                        )
                    )
            pub_id = _plain_prop(props.get(PUBLICATION_ID_FIELDS["postmypost"]))
            if pub_id:
                mappings.append(
                    PublicationMapping(
                        listing_id=listing_id,
                        page_id=page_id,
                        platform="postmypost",
                        publication_id=pub_id,
                        canonical_url=f"https://app.postmypost.io/publications/{pub_id}",
                        notion_url="",
                    )
                )
        self._index = mappings
        return mappings

    def find_by_url(self, canonical_url: str, platform: str | None = None) -> PublicationMapping | None:
        for item in self._ensure_index():
            if item.canonical_url == canonical_url and (not platform or item.platform == platform):
                return item
        return None

    def find_by_publication_id(
        self, platform: str, publication_id: str
    ) -> PublicationMapping | None:
        for item in self._ensure_index():
            if item.publication_id == publication_id and (
                item.platform == platform or platform in {"postmypost", "metricool"}
            ):
                return item
        return None

    def find_by_platform_reference(
        self, platform: str, publication_ref: str
    ) -> PublicationMapping | None:
        for item in self._ensure_index():
            norm = normalize_publication_url(item.canonical_url)
            if not norm or norm.publication_ref != publication_ref:
                continue
            if item.platform == platform or platform.startswith(item.platform):
                return item
        return None


def _plain_prop(prop: dict | None) -> str:
    if not prop:
        return ""
    t = prop.get("type")
    if t in ("rich_text", "title"):
        return "".join(x.get("plain_text", "") for x in prop.get(t, []))
    if t == "url":
        return prop.get("url") or ""
    return ""
