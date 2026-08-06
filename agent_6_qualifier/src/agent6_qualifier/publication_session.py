"""Apply publication resolution to qualifier session."""

from __future__ import annotations

from typing import Callable

from .models import Listing
from .publication_resolver import PublicationResolution
from .qualifier import Session


def apply_publication_resolution(
    session: Session,
    resolution: PublicationResolution,
    *,
    find_by_id: Callable[[str], Listing | None],
) -> bool:
    """Persist publication source on session. Returns True if listing was set."""
    if not resolution.found or not resolution.listing_id:
        return False

    session.source_platform = resolution.platform or ""
    session.source_publication_id = resolution.publication_id or ""
    session.source_publication_url = resolution.canonical_url or ""
    session.source_channel = resolution.source or session.source_channel or ""
    session.resolution_confidence = resolution.confidence

    listing = find_by_id(resolution.listing_id)
    if listing is not None:
        session.chosen = listing
        session.lead.preferred_object_id = listing.object_id
        if not session.lead.source_channel:
            session.lead.source_channel = resolution.platform or resolution.source or ""
        return True

    session.lead.preferred_object_id = resolution.listing_id
    if not session.lead.source_channel:
        session.lead.source_channel = resolution.platform or resolution.source or ""
    return False
