"""Read-only Agent 10 adapter over Agent 4 Publication Ledger.

Ledger ownership: agent_4_publisher/data/publications.sqlite3
Agent 10 never writes lifecycle rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent10_marketer.models import (
    AGENT10_SOURCE_PLATFORMS,
    ContentFormat,
    Platform,
    PublicationRecord,
)

# Default ledger path relative to refactor repo layout:
#   Агент 10/../agent_4_publisher/data/publications.sqlite3
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_LEDGER = _REPO_ROOT / "agent_4_publisher" / "data" / "publications.sqlite3"
_LEDGER_MODULE = _REPO_ROOT / "agent_4_publisher" / "scripts" / "publication_ledger.py"


def default_publisher_ledger_path() -> Path:
    return _DEFAULT_LEDGER


def _parse_platform(value: str | None) -> Platform:
    raw = (value or "").lower()
    if raw == "instagram":
        return Platform.INSTAGRAM
    if raw == "facebook":
        return Platform.FACEBOOK
    if raw == "tiktok":
        return Platform.TIKTOK
    return Platform.UNKNOWN


def _parse_format(value: str | None) -> ContentFormat:
    raw = (value or "").lower()
    if raw == "reel":
        return ContentFormat.REEL
    if raw == "carousel":
        return ContentFormat.CAROUSEL
    if raw == "post":
        return ContentFormat.POST
    if raw == "story":
        return ContentFormat.STORY
    return ContentFormat.UNKNOWN


def _parse_dt(value: str | None):
    if not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def row_to_publication_record(row: Any) -> PublicationRecord:
    platform = _parse_platform(getattr(row, "platform", None))
    fmt = _parse_format(getattr(row, "format", None))
    permalink = getattr(row, "permalink", None)
    return PublicationRecord(
        publication_id=str(getattr(row, "publication_key")),
        object_id=str(getattr(row, "object_id") or ""),
        platform=platform,
        format=fmt,
        postmypost_post_id=str(getattr(row, "postmypost_publication_id") or "") or None,
        instagram_media_id=(
            getattr(row, "external_media_id", None) if platform == Platform.INSTAGRAM else None
        ),
        facebook_post_id=(
            getattr(row, "external_media_id", None) if platform == Platform.FACEBOOK else None
        ),
        instagram_permalink=permalink if platform == Platform.INSTAGRAM else None,
        permalink=permalink,
        published_at=_parse_dt(getattr(row, "published_at", None)),
        status=getattr(row, "status", None),
        source="publication_ledger",
    )


class PublisherLedgerReader:
    """Read-only view of Agent 4 PublicationLedger (IG/FB scope for Agent 10)."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else default_publisher_ledger_path()
        self._ledger = None

    @property
    def available(self) -> bool:
        return self.db_path.exists()

    def _load(self):
        if self._ledger is not None:
            return self._ledger
        import importlib.util
        import sys

        module_path = _LEDGER_MODULE
        if not module_path.exists():
            raise RuntimeError(f"cannot find publication_ledger module at {module_path}")
        scripts_dir = module_path.parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        spec = importlib.util.spec_from_file_location(
            "agent4_publication_ledger", module_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load publication_ledger from {module_path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        self._ledger = mod.PublicationLedger(self.db_path)
        return self._ledger

    def get_publications(
        self,
        object_id: str,
        *,
        platform: str | None = None,
        format: str | None = None,
        agent10_scope_only: bool = True,
    ) -> list[PublicationRecord]:
        if not self.available:
            return []
        ledger = self._load()
        rows = ledger.list_by_object(object_id, platform=platform, format=format)
        records = [row_to_publication_record(r) for r in rows]
        if agent10_scope_only:
            records = [r for r in records if r.platform in AGENT10_SOURCE_PLATFORMS]
        return records

    def get_instagram_reels(self, object_id: str) -> list[PublicationRecord]:
        return [
            r
            for r in self.get_publications(object_id, platform="instagram")
            if r.format == ContentFormat.REEL
        ]

    def get_facebook_creatives(self, object_id: str) -> list[PublicationRecord]:
        return self.get_publications(object_id, platform="facebook")

    def get_publication(self, publication_key: str) -> PublicationRecord | None:
        if not self.available:
            return None
        ledger = self._load()
        row = ledger.get_by_key(publication_key)
        if row is None:
            return None
        rec = row_to_publication_record(row)
        if rec.platform not in AGENT10_SOURCE_PLATFORMS:
            return None
        return rec

    # Explicitly no write API — mutation attempts must fail.
    def register(self, *args: Any, **kwargs: Any) -> None:
        raise PermissionError("Agent 10 ledger adapter is read-only")

    def write(self, *args: Any, **kwargs: Any) -> None:
        raise PermissionError("Agent 10 ledger adapter is read-only")
