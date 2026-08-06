"""Inbound social reference contract (Agent 5 → Agent 6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .publication_resolver import PublicationResolution, resolve_publication_reference
from .publication_mapping_store import PublicationMappingStore


@dataclass
class SocialInboundReference:
    platform: str
    sender_id: str | None = None
    text: str | None = None
    publication_id: str | None = None
    publication_url: str | None = None
    parent_post_id: str | None = None
    shared_post_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def resolve_social_inbound_reference(
    reference: SocialInboundReference,
    *,
    store: PublicationMappingStore,
) -> PublicationResolution:
    """Resolve listing from PostMyPost/social inbound event (no network)."""
    urls: list[str] = []
    if reference.publication_url:
        urls.append(reference.publication_url)
    if reference.shared_post_url:
        urls.append(reference.shared_post_url)

    meta = dict(reference.metadata)
    meta["source"] = reference.platform
    if reference.parent_post_id:
        meta["parent_post_id"] = reference.parent_post_id

    return resolve_publication_reference(
        reference.text,
        urls,
        reference.platform,
        reference.publication_id,
        meta,
        store=store,
    )
