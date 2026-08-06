"""Agent 5 → Agent 6 social inbound adapter (no PostMyPost webhook yet)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agent6_qualifier.publication_resolver import PublicationResolution
from agent6_qualifier.social_inbound import SocialInboundReference, resolve_social_inbound_reference


def build_social_inbound_reference(payload: dict[str, Any]) -> SocialInboundReference:
    """Parse documented inbound payload into SocialInboundReference."""
    return SocialInboundReference(
        platform=str(payload.get("platform") or ""),
        sender_id=payload.get("sender_id"),
        text=payload.get("text"),
        publication_id=payload.get("publication_id"),
        publication_url=payload.get("publication_url"),
        parent_post_id=payload.get("parent_post_id"),
        shared_post_url=payload.get("shared_post_url"),
        metadata=dict(payload.get("metadata") or {}),
    )


def resolve_inbound_for_agent6(
    payload: dict[str, Any],
    *,
    store: Any,
) -> dict[str, Any]:
    """Callable after PostMyPost webhook/export appears. Returns JSON-safe dict."""
    ref = build_social_inbound_reference(payload)
    resolution: PublicationResolution = resolve_social_inbound_reference(ref, store=store)
    return {
        "reference": asdict(ref),
        "resolution": asdict(resolution),
    }
