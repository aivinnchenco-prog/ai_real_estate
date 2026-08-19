"""Domain models for Agent 10."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Platform(str, Enum):
    """Platform of the *source publication* (creative), not the ads network.

    Paid ads are always Meta Ads — see SCOPE.md.
    TIKTOK / UNKNOWN may appear in a shared Publication Ledger for Publisher
    compatibility; Agent 10 analysis ignores anything outside AGENT10_SOURCE_PLATFORMS.
    """

    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TIKTOK = "tiktok"  # ledger-compatible only; ignored by Agent 10
    UNKNOWN = "unknown"


# Agent 10 query scope: source creatives only (paid platform is always Meta).
AGENT10_SOURCE_PLATFORMS: frozenset[Platform] = frozenset(
    {Platform.INSTAGRAM, Platform.FACEBOOK}
)


class ContentFormat(str, Enum):
    REEL = "reel"
    CAROUSEL = "carousel"
    POST = "post"
    STORY = "story"
    UNKNOWN = "unknown"


# V1 primary promotion candidates.
AGENT10_PRIMARY_FORMATS: frozenset[ContentFormat] = frozenset({ContentFormat.REEL})


class ApprovalState(str, Enum):
    DRAFT = "DRAFT"
    ANALYZED = "ANALYZED"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    READY_TO_LAUNCH = "READY_TO_LAUNCH"
    LAUNCHED = "LAUNCHED"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


# V1 may advance at most to READY_TO_LAUNCH while Meta is disabled.
V1_MAX_STATE = ApprovalState.READY_TO_LAUNCH


@dataclass
class PeerGroupAttributes:
    property_type: str | None = None
    district: str | None = None
    rent_type: str | None = None
    price_band: str | None = None
    bedrooms: float | None = None


@dataclass
class ObjectSummary:
    object_id: str
    page_id: str | None = None
    title: str | None = None
    peer: PeerGroupAttributes = field(default_factory=PeerGroupAttributes)
    missing_fields: list[str] = field(default_factory=list)
    raw_properties_present: list[str] = field(default_factory=list)


@dataclass
class PublicationRecord:
    """Preferred future publication record shape (partial fill OK in V1)."""

    publication_id: str
    object_id: str
    platform: Platform = Platform.INSTAGRAM
    format: ContentFormat = ContentFormat.REEL
    postmypost_post_id: str | None = None
    instagram_media_id: str | None = None
    instagram_permalink: str | None = None
    facebook_post_id: str | None = None
    permalink: str | None = None  # generic public URL when platform-specific id unknown
    published_at: datetime | None = None
    status: str | None = None
    source: str = "unknown"  # notion | postmypost | fixture | mock
    notion_field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["platform"] = self.platform.value
        d["format"] = self.format.value
        if self.published_at is not None:
            d["published_at"] = self.published_at.isoformat()
        return d


@dataclass
class AnalyticsMetrics:
    """Canonical metrics. Unavailable metrics MUST be None, never 0."""

    publication_id: str
    object_id: str
    platform: Platform
    format: ContentFormat
    published_at: datetime | None
    age_hours: float | None

    reach: float | None = None
    impressions: float | None = None
    views: float | None = None
    likes: float | None = None
    comments: float | None = None
    saves: float | None = None
    shares: float | None = None

    # Derived (None if inputs missing / zero denominator)
    save_rate: float | None = None
    comment_rate: float | None = None
    share_rate: float | None = None
    engagement_rate: float | None = None
    reach_velocity: float | None = None
    view_velocity: float | None = None

    data_quality: str = "unknown"  # full | partial | empty | unavailable
    source: str = "unknown"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["platform"] = self.platform.value
        d["format"] = self.format.value
        if self.published_at is not None:
            d["published_at"] = self.published_at.isoformat()
        return d


@dataclass
class ScoreBreakdown:
    organic_score: float
    breakdown: dict[str, float]
    used_weights: dict[str, float]
    missing_components: list[str]
    available_components: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RankedReel:
    publication: PublicationRecord
    analytics: AnalyticsMetrics
    score: ScoreBreakdown
    rank: int = 0


@dataclass
class CampaignSuggestion:
    objective: str
    daily_budget: float
    duration_days: int
    max_total_budget: float
    strategy: str
    placements_mode: str
    audience_mode: str
    currency: str = "THB"
    capped: bool = False
    cap_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Recommendation:
    object_id: str
    object_summary: ObjectSummary
    reels_found: int
    ranked: list[RankedReel]
    winner: RankedReel | None
    why_selected: list[str]
    data_quality: str
    campaign: CampaignSuggestion | None
    status: ApprovalState = ApprovalState.PROPOSED
    peer_scope: str = "same_object"
    notes: list[str] = field(default_factory=list)
    meta_launch: str = "DISABLED"
    facebook_creatives_found: int = 0
    analytics_status: str = "UNAVAILABLE"
    ranking_status: str = "BLOCKED"
    history: list[PublicationRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "object_summary": asdict(self.object_summary),
            "reels_found": self.reels_found,
            "facebook_creatives_found": self.facebook_creatives_found,
            "analytics_status": self.analytics_status,
            "ranking_status": self.ranking_status,
            "history": [p.to_dict() for p in self.history],
            "ranked": [
                {
                    "rank": r.rank,
                    "publication": r.publication.to_dict(),
                    "analytics": r.analytics.to_dict(),
                    "organic_score": r.score.organic_score,
                    "breakdown": r.score.breakdown,
                }
                for r in self.ranked
            ],
            "winner": None
            if self.winner is None
            else {
                "publication_id": self.winner.publication.publication_id,
                "organic_score": self.winner.score.organic_score,
                "permalink": self.winner.publication.instagram_permalink,
            },
            "why_selected": list(self.why_selected),
            "data_quality": self.data_quality,
            "campaign": None if self.campaign is None else self.campaign.to_dict(),
            "status": self.status.value,
            "peer_scope": self.peer_scope,
            "notes": list(self.notes),
            "meta_launch": self.meta_launch,
        }


# ── Future: Business Score (optional metrics, no fake fill) ─────────────


@dataclass
class PaidPerformanceMetrics:
    """Meta Ads Insights / business outcomes only. No other ad networks."""

    spend: float | None = None
    impressions: float | None = None
    reach: float | None = None
    cpm: float | None = None
    clicks: float | None = None
    ctr: float | None = None
    cpc: float | None = None
    leads: float | None = None
    cpl: float | None = None
    qualified_leads: float | None = None
    cost_per_qualified_lead: float | None = None
    bookings: float | None = None
    cost_per_booking: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BusinessScoreResult:
    business_score: float | None
    breakdown: dict[str, float]
    missing_components: list[str]
    sample_size: int = 0
    confidence: float | None = None


class BusinessScorer:
    """Future interface — V1 does not compute paid scores."""

    def score(self, metrics: PaidPerformanceMetrics) -> BusinessScoreResult:
        raise NotImplementedError("Business scoring is not implemented in V1")


# ── Future: Agent 6 / amoCRM domain events ──────────────────────────────


class LeadEventType(str, Enum):
    LEAD_CREATED = "lead_created"
    LEAD_QUALIFIED = "lead_qualified"
    LEAD_DISQUALIFIED = "lead_disqualified"
    BOOKING_CONFIRMED = "booking_confirmed"


@dataclass
class LeadOutcomeEvent:
    event_type: LeadEventType
    object_id: str | None = None
    lead_id: str | None = None
    campaign_id: str | None = None
    ad_id: str | None = None
    publication_id: str | None = None
    amocrm_deal_id: str | None = None
    occurred_at: datetime | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        if self.occurred_at is not None:
            d["occurred_at"] = self.occurred_at.isoformat()
        return d


# ── Future: Content feedback ────────────────────────────────────────────


@dataclass
class CreativeInsight:
    hook_type: str | None = None
    first_scene: str | None = None
    property_type: str | None = None
    district: str | None = None
    organic_score: float | None = None
    business_score: float | None = None
    sample_size: int = 0
    confidence: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalRecord:
    approval_id: str
    object_id: str
    publication_id: str
    organic_score: float
    daily_budget: float
    duration_days: int
    max_total_budget: float
    state: ApprovalState
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        if self.approved_at is not None:
            d["approved_at"] = self.approved_at.isoformat()
        if self.created_at is not None:
            d["created_at"] = self.created_at.isoformat()
        return d
