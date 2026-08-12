"""Agent 10 orchestration service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent10_marketer.adapters.meta_ads import (
    DisabledMetaAdsAdapter,
    MetaAdsAdapter,
    MetaMarketingApiAdapter,
)
from agent10_marketer.adapters.meta_errors import MetaWriteDisabled
from agent10_marketer.adapters.meta_policy import assert_budget_within_caps
from agent10_marketer.adapters.notion import MockNotionAdapter, NotionAdapter, NotionObjectStore
from agent10_marketer.adapters.postmypost import PostMyPostAdapter, StubPostMyPostAdapter
from agent10_marketer.adapters.publisher_ledger import PublisherLedgerReader
from agent10_marketer.approval import advance_to_ready, approve, attempt_launch, launch_requires_approval
from agent10_marketer.config import Agent10Config, load_config
from agent10_marketer.marketing_brain import DisabledMarketingBrain, MarketingBrain
from agent10_marketer.models import ApprovalRecord, ApprovalState, ObjectSummary, Recommendation
from agent10_marketer.persistence import Agent10Store
from agent10_marketer.recommendation import RecommendationEngine, merge_publications


class PerformanceMarketerService:
    def __init__(
        self,
        config: Agent10Config | None = None,
        *,
        notion: NotionObjectStore | None = None,
        postmypost: PostMyPostAdapter | None = None,
        meta: MetaAdsAdapter | None = None,
        store: Agent10Store | None = None,
        ledger: PublisherLedgerReader | None = None,
        brain: MarketingBrain | None = None,
    ):
        self.config = config or load_config()
        if notion is not None:
            self.notion = notion
        elif self.config.notion_token and self.config.notion_database_id:
            self.notion = NotionAdapter(
                token=self.config.notion_token,
                database_id=self.config.notion_database_id,
            )
        else:
            self.notion = MockNotionAdapter()

        if postmypost is not None:
            self.postmypost = postmypost
        elif self.config.postmypost_api_token:
            self.postmypost = StubPostMyPostAdapter(
                reason="PostMyPost analytics endpoints not confirmed in project docs"
            )
        else:
            self.postmypost = StubPostMyPostAdapter(reason="POSTMYPOST_API_TOKEN missing")

        if meta is not None:
            self.meta = meta
        elif self.config.meta.ads_enabled and self.config.meta.token_set:
            # Real adapter available for reads / future paused writes — never auto-ACTIVE.
            self.meta = MetaMarketingApiAdapter(
                self.config.meta, budget=self.config.budget
            )
        else:
            self.meta = DisabledMetaAdsAdapter()

        self.store = store or Agent10Store(self.config.data_dir / "agent10.sqlite3")
        self.ledger = ledger if ledger is not None else PublisherLedgerReader()
        self.engine = RecommendationEngine(self.config)
        self.brain = brain or DisabledMarketingBrain(
            reason=(
                "AGENT10_LLM_ENABLED=false"
                if not self.config.llm_enabled
                else "MarketingBrain provider not wired"
            )
        )

    def analyze_object(
        self,
        object_id: str,
        *,
        now: datetime | None = None,
        analytics_override: dict[str, dict[str, Any] | None] | None = None,
    ) -> Recommendation:
        object_id = str(object_id).strip()
        summary = self.notion.get_object(object_id)
        if summary is None:
            summary = ObjectSummary(
                object_id=object_id,
                missing_fields=["object_not_found_in_notion"],
            )

        notion_pubs = self.notion.get_publications(object_id)
        try:
            pmp_pubs = self.postmypost.get_publications(object_id)
        except Exception as exc:  # noqa: BLE001
            pmp_pubs = []
            summary.missing_fields.append(f"postmypost_unavailable:{exc}")

        try:
            ledger_pubs = self.ledger.get_publications(object_id)
        except Exception as exc:  # noqa: BLE001
            ledger_pubs = []
            summary.missing_fields.append(f"ledger_unavailable:{exc}")

        # Prefer ledger history (many publications), then PostMyPost adapter, then Notion current URLs.
        publications = merge_publications(notion_pubs, pmp_pubs)
        publications = merge_publications(publications, ledger_pubs)

        analytics_by_id: dict[str, dict[str, Any] | None] = {}
        if analytics_override is not None:
            analytics_by_id.update(analytics_override)
        else:
            for pub in publications:
                keys = [pub.publication_id]
                if pub.postmypost_post_id:
                    keys.append(pub.postmypost_post_id)
                raw = None
                for key in keys:
                    try:
                        raw = self.postmypost.get_post_analytics(key)
                    except Exception:  # noqa: BLE001
                        raw = None
                    if raw is not None:
                        break
                analytics_by_id[pub.publication_id] = raw

        recommendation = self.engine.analyze_reels(
            object_id=object_id,
            object_summary=summary,
            publications=publications,
            analytics_by_pub_id=analytics_by_id,
            now=now,
        )

        self.store.save_publications(object_id, [p.to_dict() for p in publications])
        self.store.save_analytics(
            object_id, [r.analytics.to_dict() for r in recommendation.ranked]
        )
        self.store.save_recommendation(object_id, recommendation.to_dict())
        return recommendation

    def create_approval_draft(self, recommendation: Recommendation) -> ApprovalRecord | None:
        if recommendation.winner is None or recommendation.campaign is None:
            return None
        w = recommendation.winner
        c = recommendation.campaign
        record = ApprovalRecord(
            approval_id=str(uuid.uuid4()),
            object_id=recommendation.object_id,
            publication_id=w.publication.publication_id,
            organic_score=w.score.organic_score,
            daily_budget=c.daily_budget,
            duration_days=c.duration_days,
            max_total_budget=c.max_total_budget,
            state=ApprovalState.PROPOSED,
            created_at=datetime.now(timezone.utc),
        )
        self.store.save_approval(record)
        return record

    def approve_recommendation(
        self,
        approval_id: str,
        *,
        approved_by: str,
    ) -> ApprovalRecord:
        record = self.store.get_approval(approval_id)
        if record is None:
            raise KeyError(f"approval not found: {approval_id}")
        approve(record, approved_by=approved_by)
        advance_to_ready(record)
        self.store.save_approval(record)
        return record

    def launch(self, approval_id: str) -> dict[str, Any]:
        record = self.store.get_approval(approval_id)
        if record is None:
            raise KeyError(f"approval not found: {approval_id}")
        return attempt_launch(
            record,
            self.meta,
            meta_enabled=self.config.meta_ads_enabled,
        )

    def create_paused_campaign_from_approval(
        self,
        approval_id: str,
        *,
        name: str | None = None,
        objective: str | None = None,
    ) -> dict[str, Any]:
        """Service-layer Meta create: requires approval + write switch; status forced PAUSED.

        LLM / MarketingBrain never call this path directly.
        """
        record = self.store.get_approval(approval_id)
        if record is None:
            raise KeyError(f"approval not found: {approval_id}")
        launch_requires_approval(record)
        if record.state == ApprovalState.APPROVED:
            advance_to_ready(record)
            self.store.save_approval(record)

        if not self.config.meta.write_enabled:
            raise MetaWriteDisabled(
                "META_WRITE_ENABLED=false — service will not create campaigns"
            )

        assert_budget_within_caps(
            self.config.budget,
            daily_budget=record.daily_budget,
            duration_days=record.duration_days,
            total_budget=record.max_total_budget,
        )

        if not isinstance(self.meta, MetaMarketingApiAdapter):
            # Mock/disabled still go through adapter create_campaign for tests.
            return self.meta.create_campaign(
                name=name or f"object_{record.object_id}_paused",
                objective=objective or self.config.budget.default_objective,
                status="PAUSED",
                daily_budget=record.daily_budget,
                duration_days=record.duration_days,
                max_total_budget=record.max_total_budget,
                approval_state=record.state,
                object_id=record.object_id,
                publication_id=record.publication_id,
            )

        return self.meta.create_campaign(
            name=name or f"object_{record.object_id}_paused",
            objective=objective or self.config.budget.default_objective,
            status="PAUSED",
            daily_budget=record.daily_budget,
            duration_days=record.duration_days,
            max_total_budget=record.max_total_budget,
            approval_state=record.state,
            object_id=record.object_id,
            publication_id=record.publication_id,
        )
