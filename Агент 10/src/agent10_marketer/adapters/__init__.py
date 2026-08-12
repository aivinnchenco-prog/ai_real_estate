"""Adapter interfaces package."""

from agent10_marketer.adapters.meta_ads import (
    DisabledMetaAdsAdapter,
    MetaAdsAdapter,
    MetaIntegrationDisabled,
    MetaMarketingApiAdapter,
    MockMetaAdsAdapter,
)
from agent10_marketer.adapters.meta_errors import (
    MetaActiveDisabled,
    MetaApiError,
    MetaApprovalRequired,
    MetaBudgetPolicyError,
    MetaPermissionDenied,
    MetaRateLimited,
    MetaTokenExpired,
    MetaTokenMissing,
    MetaWriteDisabled,
)
from agent10_marketer.adapters.meta_insights import normalize_insights
from agent10_marketer.adapters.notion import MockNotionAdapter, NotionAdapter, NotionObjectStore
from agent10_marketer.adapters.postmypost import (
    MockPostMyPostAdapter,
    PostMyPostAdapter,
    PostMyPostUnavailable,
    StubPostMyPostAdapter,
)
from agent10_marketer.adapters.publisher_ledger import PublisherLedgerReader

__all__ = [
    "DisabledMetaAdsAdapter",
    "MetaAdsAdapter",
    "MetaIntegrationDisabled",
    "MetaMarketingApiAdapter",
    "MockMetaAdsAdapter",
    "MetaActiveDisabled",
    "MetaApiError",
    "MetaApprovalRequired",
    "MetaBudgetPolicyError",
    "MetaPermissionDenied",
    "MetaRateLimited",
    "MetaTokenExpired",
    "MetaTokenMissing",
    "MetaWriteDisabled",
    "normalize_insights",
    "MockNotionAdapter",
    "NotionAdapter",
    "NotionObjectStore",
    "MockPostMyPostAdapter",
    "PostMyPostAdapter",
    "PostMyPostUnavailable",
    "StubPostMyPostAdapter",
    "PublisherLedgerReader",
]
