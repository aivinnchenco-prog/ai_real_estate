"""PostMyPost adapter interface.

Documented contract (logical methods). Real analytics endpoints are NOT invented.
Agent 4 uses POSTMYPOST_API_TOKEN + https://api.postmypost.io/v4.1 for publishing
(/projects, /accounts, /publications, …) but project docs do not define analytics
endpoints for Agent 10 V1. Until an official analytics contract is confirmed,
use Stub/Mock adapters — no fake live analytics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent10_marketer.models import PublicationRecord


class PostMyPostUnavailable(RuntimeError):
    """Raised when PostMyPost credentials/API analytics contract are unavailable."""


class PostMyPostAdapter(ABC):
    """Logical interface — do not assume undocumented HTTP paths."""

    @abstractmethod
    def get_publications(self, object_id: str) -> list[PublicationRecord]:
        raise NotImplementedError

    @abstractmethod
    def get_post_analytics(self, post_id: str) -> dict[str, Any] | None:
        """Return raw metric dict or None if unavailable. Missing metrics omitted (not 0)."""
        raise NotImplementedError

    @abstractmethod
    def get_account_analytics(self, **kwargs: Any) -> dict[str, Any] | None:
        raise NotImplementedError


class StubPostMyPostAdapter(PostMyPostAdapter):
    """Default V1 adapter: credentials may exist for publishing, but analytics are unavailable."""

    def __init__(self, *, reason: str = "PostMyPost analytics contract not configured"):
        self.reason = reason
        self.network_calls = 0

    def get_publications(self, object_id: str) -> list[PublicationRecord]:
        self.network_calls += 0  # no network
        return []

    def get_post_analytics(self, post_id: str) -> dict[str, Any] | None:
        return None

    def get_account_analytics(self, **kwargs: Any) -> dict[str, Any] | None:
        return None


class MockPostMyPostAdapter(PostMyPostAdapter):
    """Test/offline fixture adapter — no network I/O."""

    def __init__(
        self,
        publications: dict[str, list[PublicationRecord]] | None = None,
        analytics: dict[str, dict[str, Any]] | None = None,
        account_analytics: dict[str, Any] | None = None,
    ):
        self.publications = publications or {}
        self.analytics = analytics or {}
        self.account_analytics = account_analytics
        self.network_calls = 0

    def get_publications(self, object_id: str) -> list[PublicationRecord]:
        return list(self.publications.get(str(object_id), []))

    def get_post_analytics(self, post_id: str) -> dict[str, Any] | None:
        return self.analytics.get(str(post_id))

    def get_account_analytics(self, **kwargs: Any) -> dict[str, Any] | None:
        return self.account_analytics
