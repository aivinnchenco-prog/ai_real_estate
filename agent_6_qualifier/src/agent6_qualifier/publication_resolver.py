"""Resolve social publication references to listing IDs."""

from __future__ import annotations

from dataclasses import dataclass, field

from .object_id import extract_object_ids, extract_tg_post, extract_utm_campaign
from .publication_mapping_store import PublicationMapping, PublicationMappingStore
from .publication_url_normalize import (
    NormalizedPublicationUrl,
    UrlExpander,
    extract_urls_from_text,
    normalize_publication_url,
    telegram_forward_url,
)


@dataclass
class PublicationResolution:
    found: bool
    listing_id: str | None = None
    platform: str | None = None
    publication_id: str | None = None
    canonical_url: str | None = None
    source: str | None = None
    confidence: str = "not_found"
    reason: str | None = None
    page_id: str | None = None
    notion_url: str | None = None
    candidates: list[str] = field(default_factory=list)


def _resolution_from_mapping(
    mapping: PublicationMapping,
    *,
    confidence: str,
    source: str,
) -> PublicationResolution:
    return PublicationResolution(
        found=True,
        listing_id=mapping.listing_id,
        platform=mapping.platform,
        publication_id=mapping.publication_id,
        canonical_url=mapping.canonical_url,
        source=source,
        confidence=confidence,
        page_id=mapping.page_id,
        notion_url=mapping.notion_url or mapping.canonical_url,
    )


def resolve_publication_reference(
    text: str | None,
    urls: list[str] | None,
    platform: str | None,
    publication_id: str | None,
    message_metadata: dict | None,
    *,
    store: PublicationMappingStore,
    url_expander: UrlExpander | None = None,
) -> PublicationResolution:
    """Resolve publication → listing without LLM guessing."""
    meta = message_metadata or {}
    hits: list[PublicationResolution] = []

    if publication_id and platform:
        mapping = store.find_by_publication_id(platform, publication_id)
        if mapping:
            return _resolution_from_mapping(
                mapping, confidence="exact_publication_id", source="publication_id"
            )

    all_urls = list(urls or [])
    all_urls.extend(extract_urls_from_text(text))
    fwd_channel = meta.get("forward_channel")
    fwd_msg_id = meta.get("forward_message_id")
    fwd_url = telegram_forward_url(fwd_channel, fwd_msg_id)
    if fwd_url:
        all_urls.append(fwd_url)

    seen_urls: list[str] = []
    for raw in all_urls:
        if raw not in seen_urls:
            seen_urls.append(raw)

    for raw in seen_urls:
        norm = normalize_publication_url(raw, expander=url_expander)
        if norm is None:
            continue
        if norm.unresolved_short:
            return PublicationResolution(
                found=False,
                canonical_url=raw,
                confidence="not_found",
                reason="unresolved_short_url",
            )
        hit = _lookup_normalized(norm, store, source_hint=meta.get("source"))
        if hit and hit.found:
            hits.append(hit)

    if hits:
        listing_ids = []
        for hit in hits:
            if hit.listing_id and hit.listing_id not in listing_ids:
                listing_ids.append(hit.listing_id)
        if len(listing_ids) == 1:
            return hits[0]
        return PublicationResolution(
            found=False,
            confidence="ambiguous",
            reason="multiple_objects",
            candidates=listing_ids,
        )

    # Telegram forward by channel/message without URL in text
    tg = extract_tg_post(fwd_url or (text or ""))
    if tg:
        norm = normalize_publication_url(f"https://t.me/{tg[0]}/{tg[1]}")
        if norm:
            hit = _lookup_normalized(norm, store, source_hint="telegram_forward")
            if hit.found:
                hit.confidence = "telegram_forward"
                return hit

    utm_oid = extract_utm_campaign(text or "")
    if utm_oid:
        return PublicationResolution(
            found=True,
            listing_id=utm_oid,
            confidence="object_id_in_text",
            source="utm_campaign",
            reason=None,
        )

    ids = extract_object_ids(text or "")
    if len(ids) == 1:
        return PublicationResolution(
            found=True,
            listing_id=ids[0],
            confidence="object_id_in_text",
            source="object_id",
        )
    if len(ids) > 1:
        return PublicationResolution(
            found=False,
            confidence="ambiguous",
            reason="multiple_object_ids",
            candidates=ids,
        )

    if platform and meta.get("parent_post_id"):
        mapping = store.find_by_platform_reference(platform, str(meta["parent_post_id"]))
        if mapping:
            return _resolution_from_mapping(
                mapping, confidence="platform_metadata", source="parent_post_id"
            )

    return PublicationResolution(
        found=False,
        confidence="not_found",
        reason="no_match",
        platform=platform,
        publication_id=publication_id,
    )


def _lookup_normalized(
    norm: NormalizedPublicationUrl,
    store: PublicationMappingStore,
    *,
    source_hint: str | None,
) -> PublicationResolution:
    mapping = store.find_by_url(norm.canonical_url, norm.platform)
    if not mapping and norm.publication_ref:
        mapping = store.find_by_platform_reference(norm.platform, norm.publication_ref)
    if mapping:
        source = source_hint or norm.platform
        return _resolution_from_mapping(
            mapping, confidence="exact_url", source=source
        )
    return PublicationResolution(
        found=False,
        platform=norm.platform,
        canonical_url=norm.canonical_url,
        confidence="not_found",
        reason="url_not_in_mapping",
    )
