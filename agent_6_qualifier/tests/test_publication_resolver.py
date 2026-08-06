"""Offline tests for publication URL resolver."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.models import Listing
from agent6_qualifier.publication_mapping_store import (
    InMemoryPublicationMappingStore,
    PublicationMapping,
)
from agent6_qualifier.publication_resolver import resolve_publication_reference
from agent6_qualifier.publication_session import apply_publication_resolution
from agent6_qualifier.publication_url_normalize import (
    extract_urls_from_text,
    normalize_publication_url,
    telegram_forward_url,
)
from agent6_qualifier.qualifier import Session
from agent6_qualifier.social_inbound import (
    SocialInboundReference,
    resolve_social_inbound_reference,
)


def _store() -> InMemoryPublicationMappingStore:
    return InMemoryPublicationMappingStore(
        [
            PublicationMapping(
                listing_id="A_20260713_003",
                page_id="page-1",
                platform="instagram_reel",
                publication_id=None,
                canonical_url="https://instagram.com/reel/ABC123",
                notion_url="https://www.instagram.com/reel/ABC123/",
            ),
            PublicationMapping(
                listing_id="20260708_001",
                page_id="page-2",
                platform="tiktok",
                publication_id=None,
                canonical_url="https://tiktok.com/@openhome/video/999",
                notion_url="https://www.tiktok.com/@openhome/video/999",
            ),
            PublicationMapping(
                listing_id="20260708_002",
                page_id="page-3",
                platform="postmypost",
                publication_id="555001",
                canonical_url="https://app.postmypost.io/publications/555001",
                notion_url="",
            ),
            PublicationMapping(
                listing_id="20260709_003",
                page_id="page-4",
                platform="telegram",
                publication_id=None,
                canonical_url="https://t.me/OpenHome_th/42",
                notion_url="https://t.me/OpenHome_th/42",
            ),
        ]
    )


@pytest.mark.parametrize(
    "raw,platform,canonical",
    [
        (
            "https://www.instagram.com/reel/ABC123/?utm_source=ig",
            "instagram_reel",
            "https://instagram.com/reel/ABC123",
        ),
        (
            "https://instagram.com/p/XYZ789/",
            "instagram_carousel",
            "https://instagram.com/p/XYZ789",
        ),
        (
            "https://www.tiktok.com/@openhome/video/999?lang=en",
            "tiktok",
            "https://tiktok.com/@openhome/video/999",
        ),
        (
            "https://youtu.be/abcdEFG12",
            "youtube",
            "https://youtube.com/watch?v=abcdEFG12",
        ),
        (
            "https://www.youtube.com/shorts/abcdEFG12",
            "youtube",
            "https://youtube.com/shorts/abcdEFG12",
        ),
        (
            "https://x.com/user/status/1122334455?s=20",
            "x",
            "https://x.com/user/status/1122334455",
        ),
        (
            "https://www.facebook.com/groups/123/posts/456/",
            "fb_groups",
            "https://facebook.com/groups/123/posts/456",
        ),
        (
            "https://t.me/OpenHome_th/42",
            "telegram",
            "https://t.me/OpenHome_th/42",
        ),
    ],
)
def test_normalize_publication_url(raw: str, platform: str, canonical: str) -> None:
    norm = normalize_publication_url(raw)
    assert norm is not None
    assert norm.platform == platform
    assert norm.canonical_url == canonical


def test_short_url_without_expander() -> None:
    norm = normalize_publication_url("https://vm.tiktok.com/ZZZZ/")
    assert norm is not None
    assert norm.unresolved_short is True


def test_resolve_exact_url() -> None:
    store = _store()
    out = resolve_publication_reference(
        "смотрите https://www.instagram.com/reel/ABC123/",
        None,
        None,
        None,
        None,
        store=store,
    )
    assert out.found is True
    assert out.listing_id == "A_20260713_003"
    assert out.confidence == "exact_url"


def test_resolve_publication_id() -> None:
    store = _store()
    out = resolve_publication_reference(
        None,
        None,
        "postmypost",
        "555001",
        None,
        store=store,
    )
    assert out.found is True
    assert out.listing_id == "20260708_002"
    assert out.confidence == "exact_publication_id"


def test_resolve_telegram_forward() -> None:
    store = _store()
    out = resolve_publication_reference(
        "",
        None,
        "telegram",
        None,
        {"forward_channel": "OpenHome_th", "forward_message_id": 42},
        store=store,
    )
    assert out.found is True
    assert out.listing_id == "20260709_003"
    assert out.confidence == "exact_url"


def test_resolve_ambiguous_urls() -> None:
    store = _store()
    text = (
        "https://www.instagram.com/reel/ABC123 "
        "https://www.tiktok.com/@openhome/video/999"
    )
    out = resolve_publication_reference(text, None, None, None, None, store=store)
    assert out.found is False
    assert out.confidence == "ambiguous"
    assert len(out.candidates) == 2


def test_resolve_object_id_in_text() -> None:
    store = _store()
    out = resolve_publication_reference(
        "интересует A_20260713_003",
        None,
        None,
        None,
        None,
        store=store,
    )
    assert out.found is True
    assert out.confidence == "object_id_in_text"


def test_apply_to_session() -> None:
    session = Session(chat_id="1")
    listing = Listing(object_id="A_20260713_003", title="Villa")
    resolution = resolve_publication_reference(
        "https://www.instagram.com/reel/ABC123",
        None,
        None,
        None,
        None,
        store=_store(),
    )
    assert apply_publication_resolution(
        session,
        resolution,
        find_by_id=lambda oid: listing if oid == listing.object_id else None,
    )
    assert session.chosen is listing
    assert session.lead.preferred_object_id == "A_20260713_003"
    assert session.source_platform == "instagram_reel"


def test_social_inbound_parent_post() -> None:
    store = _store()
    ref = SocialInboundReference(
        platform="instagram_reel",
        text="цена?",
        parent_post_id="ABC123",
    )
    out = resolve_social_inbound_reference(ref, store=store)
    assert out.found is True
    assert out.listing_id == "A_20260713_003"


def test_extract_multiple_urls() -> None:
    text = "one https://t.me/ch/1 two https://instagram.com/reel/x"
    urls = extract_urls_from_text(text)
    assert len(urls) == 2


def test_telegram_forward_url_helper() -> None:
    assert telegram_forward_url("OpenHome_th", 42) == "https://t.me/OpenHome_th/42"
