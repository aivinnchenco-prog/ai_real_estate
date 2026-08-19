"""Recommendation engine — deterministic ranking + metric-tied reasons."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent10_marketer.budget import suggest_campaign
from agent10_marketer.config import Agent10Config, ScoringConfig
from agent10_marketer.models import (
    AGENT10_SOURCE_PLATFORMS,
    AnalyticsMetrics,
    ApprovalState,
    CampaignSuggestion,
    ContentFormat,
    ObjectSummary,
    Platform,
    PublicationRecord,
    RankedReel,
    Recommendation,
)
from agent10_marketer.rates import build_analytics
from agent10_marketer.scorer import CreativeScorer


def merge_publications(
    notion_pubs: list[PublicationRecord],
    pmp_pubs: list[PublicationRecord],
) -> list[PublicationRecord]:
    """Prefer PostMyPost records when same permalink/id; keep Notion-only extras."""
    by_key: dict[str, PublicationRecord] = {}

    def key(p: PublicationRecord) -> str:
        if p.postmypost_post_id:
            return f"pmp:{p.postmypost_post_id}"
        if p.instagram_permalink:
            return f"url:{p.instagram_permalink}"
        if p.permalink:
            return f"url:{p.permalink}"
        return p.publication_id

    for p in notion_pubs:
        by_key[key(p)] = p
    for p in pmp_pubs:
        by_key[key(p)] = p
    return list(by_key.values())


def filter_agent10_sources(
    publications: list[PublicationRecord],
) -> list[PublicationRecord]:
    """Publication Ledger may contain TikTok/etc.; Agent 10 keeps IG + FB only."""
    return [p for p in publications if p.platform in AGENT10_SOURCE_PLATFORMS]


def filter_reels(publications: list[PublicationRecord]) -> list[PublicationRecord]:
    """V1 primary CreativeScorer candidates: Instagram Reels only."""
    return [
        p
        for p in filter_agent10_sources(publications)
        if p.format == ContentFormat.REEL and p.platform == Platform.INSTAGRAM
    ]


def filter_facebook_creatives(
    publications: list[PublicationRecord],
) -> list[PublicationRecord]:
    """Facebook source creatives (separate scoring path; not mixed into Reel scorer)."""
    return [p for p in publications if p.platform == Platform.FACEBOOK]


def build_reasons(
    winner: RankedReel,
    ranked: list[RankedReel],
) -> list[str]:
    """Every reason must reference a computed metric present on the winner."""
    reasons: list[str] = []
    w = winner.analytics
    peers = [r for r in ranked if r.publication.publication_id != winner.publication.publication_id]

    def better(metric: str, label: str) -> None:
        wv = getattr(w, metric, None)
        if wv is None:
            return
        if not peers:
            reasons.append(f"{label}: {wv:.4g} (единственный Reel с этой метрикой)")
            return
        others = [getattr(p.analytics, metric) for p in peers]
        others_ok = [o for o in others if o is not None]
        if others_ok and wv >= max(others_ok):
            reasons.append(f"{label}: {wv:.4g} — выше или равен остальным в группе")
        elif not others_ok:
            reasons.append(f"{label}: {wv:.4g} (у остальных метрика недоступна)")

    better("save_rate", "save_rate")
    better("engagement_rate", "engagement_rate")
    better("reach_velocity", "reach_velocity")
    better("comment_rate", "comment_rate")
    better("share_rate", "share_rate")
    better("view_velocity", "view_velocity")

    if w.age_hours is not None:
        reasons.append(f"age_hours: {w.age_hours:.2f} (учтено в velocity-нормализации)")

    reasons.append(
        f"organic_score: {winner.score.organic_score:.2f}/100 "
        f"(компоненты: {', '.join(winner.score.available_components) or 'none'})"
    )
    return reasons


def overall_data_quality(items: list[AnalyticsMetrics]) -> str:
    if not items:
        return "empty"
    qualities = {a.data_quality for a in items}
    if qualities == {"full"}:
        return "full"
    if "unavailable" in qualities and len(qualities) == 1:
        return "unavailable"
    if "empty" in qualities and len(qualities) == 1:
        return "empty"
    return "partial"


class RecommendationEngine:
    def __init__(self, config: Agent10Config):
        self.config = config
        self.scorer = CreativeScorer(config.scoring)

    def analyze_reels(
        self,
        *,
        object_id: str,
        object_summary: ObjectSummary,
        publications: list[PublicationRecord],
        analytics_by_pub_id: dict[str, dict[str, Any] | None],
        now: datetime | None = None,
    ) -> Recommendation:
        reels = filter_reels(publications)
        notes: list[str] = []

        if not publications:
            notes.append("empty publication list")
        if publications and not reels:
            notes.append("no Instagram Reels found")

        analytics_list: list[AnalyticsMetrics] = []
        for pub in reels:
            raw = analytics_by_pub_id.get(pub.publication_id)
            # Also try postmypost id key
            if raw is None and pub.postmypost_post_id:
                raw = analytics_by_pub_id.get(pub.postmypost_post_id)
            source = "mock" if raw is not None else "unavailable"
            if raw is None:
                notes.append(f"analytics unavailable for {pub.publication_id}")
            analytics_list.append(
                build_analytics(
                    publication_id=pub.publication_id,
                    object_id=object_id,
                    platform=pub.platform,
                    format=pub.format,
                    published_at=pub.published_at,
                    raw=raw,
                    config=self.config.scoring,
                    source=source,
                    now=now,
                )
            )

        ranked: list[RankedReel] = []
        for pub, analytics in zip(reels, analytics_list):
            peers = [a for a in analytics_list if a.publication_id != analytics.publication_id]
            score = self.scorer.score(analytics, peers=peers or None)
            ranked.append(RankedReel(publication=pub, analytics=analytics, score=score))

        # Deterministic: sort by organic_score desc, then publication_id asc
        ranked.sort(key=lambda r: (-r.score.organic_score, r.publication.publication_id))
        for i, item in enumerate(ranked, start=1):
            item.rank = i

        any_analytics = any(
            a.data_quality not in {"empty", "unavailable"} for a in analytics_list
        )
        facebook = filter_facebook_creatives(publications)
        scoped = filter_agent10_sources(publications)

        winner = None
        why: list[str] = []
        campaign: CampaignSuggestion | None = None
        status = ApprovalState.ANALYZED
        analytics_status = "AVAILABLE" if any_analytics else "UNAVAILABLE"
        ranking_status = "READY" if any_analytics and ranked else "BLOCKED"

        if not any_analytics:
            why = [
                "Organic ranking BLOCKED — PostMyPost analytics contract is not connected.",
            ]
            if reels:
                why.append(
                    f"Publication history READY: {len(reels)} Instagram Reel(s), "
                    f"{len(facebook)} Facebook creative(s)."
                )
            ranked = []  # do not present fake organic ranking
        elif ranked:
            winner = ranked[0]
            why = build_reasons(winner, ranked)
            campaign = suggest_campaign(self.config.budget)
            status = ApprovalState.PROPOSED
            ranking_status = "READY"
        else:
            why = ["Нет Instagram Reels для сравнения — рекомендация не сформирована."]
            ranking_status = "BLOCKED"

        return Recommendation(
            object_id=object_id,
            object_summary=object_summary,
            reels_found=len(reels),
            ranked=ranked,
            winner=winner,
            why_selected=why,
            data_quality=overall_data_quality(analytics_list),
            campaign=campaign,
            status=status,
            peer_scope="same_object",
            notes=notes,
            meta_launch="DISABLED",
            facebook_creatives_found=len(facebook),
            analytics_status=analytics_status,
            ranking_status=ranking_status,
            history=scoped,
        )
