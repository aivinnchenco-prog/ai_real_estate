"""Core scoring, ranking, budget, approval, isolation tests for Agent 10."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent10_marketer.adapters.meta_ads import (
    DisabledMetaAdsAdapter,
    MetaIntegrationDisabled,
    MockMetaAdsAdapter,
)
from agent10_marketer.adapters.notion import (
    MockNotionAdapter,
    parse_object_page,
    publications_from_notion_page,
)
from agent10_marketer.adapters.postmypost import MockPostMyPostAdapter, StubPostMyPostAdapter
from agent10_marketer.approval import ApprovalError, attempt_launch, approve, advance_to_ready
from agent10_marketer.budget import suggest_campaign
from agent10_marketer.config import BudgetConfig, ScoringConfig
from agent10_marketer.models import (
    ApprovalRecord,
    ApprovalState,
    ContentFormat,
    ObjectSummary,
    PaidPerformanceMetrics,
    Platform,
    PublicationRecord,
)
from agent10_marketer.normalization import normalize_by_age
from agent10_marketer.rates import build_analytics, compute_rates, safe_div
from agent10_marketer.recommendation import (
    RecommendationEngine,
    filter_agent10_sources,
    filter_facebook_creatives,
    filter_reels,
)
from agent10_marketer.report import format_report
from agent10_marketer.scorer import CreativeScorer
from agent10_marketer.service import PerformanceMarketerService


def make_reel(
    object_id: str,
    suffix: str,
    *,
    published_at: datetime,
    postmypost_post_id: str | None = None,
) -> PublicationRecord:
    return PublicationRecord(
        publication_id=f"pub:{object_id}:{suffix}",
        object_id=object_id,
        platform=Platform.INSTAGRAM,
        format=ContentFormat.REEL,
        postmypost_post_id=postmypost_post_id or f"pmp_{suffix}",
        instagram_permalink=f"https://www.instagram.com/reel/{suffix}/",
        permalink=f"https://www.instagram.com/reel/{suffix}/",
        published_at=published_at,
        status="published",
        source="mock",
    )


# ---------------------------------------------------------------------------
# Rates / missing metrics / no div-by-zero
# ---------------------------------------------------------------------------


def test_missing_metric_handled_as_none(config):
    a = build_analytics(
        publication_id="p1",
        object_id="1847",
        platform=Platform.INSTAGRAM,
        format=ContentFormat.REEL,
        published_at=datetime.now(timezone.utc),
        raw={"views": 100, "likes": 10},  # saves/shares absent
        config=config.scoring,
    )
    assert a.saves is None
    assert a.shares is None
    assert a.views == 100
    assert a.save_rate is None  # saves missing → rate None, not 0


def test_no_division_by_zero():
    assert safe_div(10, 0) is None
    assert safe_div(None, 5) is None
    assert safe_div(10, None) is None
    assert safe_div(10, 5) == 2.0


def test_save_rate_calculation(config):
    rates = compute_rates(
        reach=1000,
        impressions=2000,
        views=3000,
        likes=50,
        comments=10,
        saves=40,
        shares=5,
        age_h=10,
        config=config.scoring,
    )
    # denominator priority: impressions
    assert rates["save_rate"] == pytest.approx(40 / 2000)


def test_comment_rate_calculation(config):
    rates = compute_rates(
        reach=None,
        impressions=None,
        views=1000,
        likes=None,
        comments=25,
        saves=None,
        shares=None,
        age_h=5,
        config=config.scoring,
    )
    assert rates["comment_rate"] == pytest.approx(25 / 1000)


def test_share_rate_optional(config):
    rates = compute_rates(
        reach=500,
        impressions=500,
        views=500,
        likes=10,
        comments=2,
        saves=5,
        shares=None,
        age_h=8,
        config=config.scoring,
    )
    assert rates["share_rate"] is None
    assert rates["save_rate"] is not None


def test_age_normalization():
    assert normalize_by_age(100, 10, min_age_hours=1) == pytest.approx(10)
    assert normalize_by_age(100, 0.1, min_age_hours=1) == pytest.approx(100)
    assert normalize_by_age(None, 10, min_age_hours=1) is None
    assert normalize_by_age(100, None, min_age_hours=1) is None


def test_newer_older_comparison_velocity(config, now):
    older = build_analytics(
        publication_id="old",
        object_id="1847",
        platform=Platform.INSTAGRAM,
        format=ContentFormat.REEL,
        published_at=now - timedelta(hours=100),
        raw={"reach": 1000, "impressions": 1000, "views": 1000, "likes": 10, "comments": 1, "saves": 5},
        config=config.scoring,
        now=now,
    )
    newer = build_analytics(
        publication_id="new",
        object_id="1847",
        platform=Platform.INSTAGRAM,
        format=ContentFormat.REEL,
        published_at=now - timedelta(hours=10),
        raw={"reach": 1000, "impressions": 1000, "views": 1000, "likes": 10, "comments": 1, "saves": 5},
        config=config.scoring,
        now=now,
    )
    assert newer.reach_velocity is not None and older.reach_velocity is not None
    assert newer.reach_velocity > older.reach_velocity


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


def test_deterministic_score(config):
    scorer = CreativeScorer(config.scoring)
    a = build_analytics(
        publication_id="p",
        object_id="1847",
        platform=Platform.INSTAGRAM,
        format=ContentFormat.REEL,
        published_at=datetime.now(timezone.utc) - timedelta(hours=24),
        raw={"reach": 1e3, "impressions": 1e3, "views": 2e3, "likes": 50, "comments": 10, "saves": 20, "shares": 5},
        config=config.scoring,
    )
    s1 = scorer.score(a)
    s2 = scorer.score(a)
    assert s1.organic_score == s2.organic_score
    assert s1.breakdown == s2.breakdown


def test_score_0_to_100(config):
    scorer = CreativeScorer(config.scoring)
    a = build_analytics(
        publication_id="p",
        object_id="1847",
        platform=Platform.INSTAGRAM,
        format=ContentFormat.REEL,
        published_at=datetime.now(timezone.utc) - timedelta(hours=24),
        raw={"reach": 100, "impressions": 100, "views": 100, "likes": 1, "comments": 0, "saves": 1},
        config=config.scoring,
    )
    s = scorer.score(a)
    assert 0 <= s.organic_score <= 100


def test_weights_configurable(config):
    custom = ScoringConfig(weights={"save_rate": 1.0, "comment_rate": 0.0})
    scorer = CreativeScorer(custom)
    a = build_analytics(
        publication_id="p",
        object_id="1847",
        platform=Platform.INSTAGRAM,
        format=ContentFormat.REEL,
        published_at=datetime.now(timezone.utc) - timedelta(hours=24),
        raw={"impressions": 100, "saves": 10, "comments": 50},
        config=custom,
    )
    s = scorer.score(a)
    assert "save_rate" in s.used_weights
    assert s.used_weights.get("save_rate", 0) == pytest.approx(1.0)


def test_missing_metric_weight_normalization(config):
    scorer = CreativeScorer(
        ScoringConfig(
            weights={"save_rate": 0.5, "share_rate": 0.5, "comment_rate": 0.0},
            rate_denominator_priority=["impressions", "reach", "views"],
            min_age_hours=1,
        )
    )
    a = build_analytics(
        publication_id="p",
        object_id="1847",
        platform=Platform.INSTAGRAM,
        format=ContentFormat.REEL,
        published_at=datetime.now(timezone.utc) - timedelta(hours=24),
        raw={"impressions": 100, "saves": 10},  # shares missing
        config=config.scoring,
    )
    s = scorer.score(a)
    assert "share_rate" in s.missing_components
    assert "save_rate" in s.used_weights
    assert s.used_weights["save_rate"] == pytest.approx(1.0)
    assert "share_rate" not in s.used_weights


# ---------------------------------------------------------------------------
# Ranking / winner
# ---------------------------------------------------------------------------


def _run_rank(config, object_summary, pubs, analytics, now):
    engine = RecommendationEngine(config)
    return engine.analyze_reels(
        object_id="1847",
        object_summary=object_summary,
        publications=pubs,
        analytics_by_pub_id=analytics,
        now=now,
    )


def test_same_object_multiple_reels_ranking(config, object_summary, now):
    pubs = [
        make_reel("1847", "a", published_at=now - timedelta(hours=48)),
        make_reel("1847", "b", published_at=now - timedelta(hours=24)),
        make_reel("1847", "c", published_at=now - timedelta(hours=12)),
    ]
    analytics = {
        pubs[0].publication_id: {
            "reach": 1000, "impressions": 1000, "views": 2000,
            "likes": 50, "comments": 5, "saves": 10, "shares": 2,
        },
        pubs[1].publication_id: {
            "reach": 2000, "impressions": 2000, "views": 3000,
            "likes": 120, "comments": 30, "saves": 80, "shares": 20,
        },
        pubs[2].publication_id: {
            "reach": 800, "impressions": 800, "views": 1000,
            "likes": 20, "comments": 2, "saves": 4, "shares": 1,
        },
    }
    rec = _run_rank(config, object_summary, pubs, analytics, now)
    assert rec.reels_found == 3
    assert len(rec.ranked) == 3
    assert rec.ranked[0].rank == 1
    scores = [r.score.organic_score for r in rec.ranked]
    assert scores == sorted(scores, reverse=True)


def test_winner_selected_by_score(config, object_summary, now):
    pubs = [
        make_reel("1847", "low", published_at=now - timedelta(hours=24)),
        make_reel("1847", "high", published_at=now - timedelta(hours=24)),
    ]
    analytics = {
        pubs[0].publication_id: {
            "reach": 1000, "impressions": 1000, "views": 1000,
            "likes": 10, "comments": 1, "saves": 2, "shares": 0,
        },
        pubs[1].publication_id: {
            "reach": 1000, "impressions": 1000, "views": 1000,
            "likes": 100, "comments": 40, "saves": 80, "shares": 30,
        },
    }
    rec = _run_rank(config, object_summary, pubs, analytics, now)
    assert rec.winner is not None
    assert rec.winner.publication.publication_id == pubs[1].publication_id
    assert rec.winner.score.organic_score >= rec.ranked[-1].score.organic_score


def test_total_views_alone_cannot_force_winner(config, object_summary, now):
    pubs = [
        make_reel("1847", "viral_views", published_at=now - timedelta(hours=72)),
        make_reel("1847", "quality", published_at=now - timedelta(hours=24)),
    ]
    analytics = {
        pubs[0].publication_id: {
            "reach": 5000, "impressions": 5000, "views": 1_000_000,
            "likes": 50, "comments": 2, "saves": 5, "shares": 1,
        },
        pubs[1].publication_id: {
            "reach": 3000, "impressions": 3000, "views": 8000,
            "likes": 400, "comments": 80, "saves": 200, "shares": 60,
        },
    }
    rec = _run_rank(config, object_summary, pubs, analytics, now)
    assert rec.winner is not None
    assert rec.winner.publication.publication_id == pubs[1].publication_id


def test_empty_publication_list(config, object_summary, now):
    rec = _run_rank(config, object_summary, [], {}, now)
    assert rec.reels_found == 0
    assert rec.winner is None
    assert "empty publication list" in rec.notes


def test_no_reels(config, object_summary, now):
    carousel = PublicationRecord(
        publication_id="car",
        object_id="1847",
        platform=Platform.INSTAGRAM,
        format=ContentFormat.CAROUSEL,
        published_at=now,
        source="mock",
    )
    rec = _run_rank(config, object_summary, [carousel], {}, now)
    assert rec.reels_found == 0
    assert rec.winner is None
    assert any("no Instagram Reels" in n for n in rec.notes)


def test_only_one_reel(config, object_summary, now):
    pubs = [make_reel("1847", "only", published_at=now - timedelta(hours=20))]
    analytics = {
        pubs[0].publication_id: {
            "reach": 1000, "impressions": 1000, "views": 2000,
            "likes": 50, "comments": 5, "saves": 20, "shares": 3,
        }
    }
    rec = _run_rank(config, object_summary, pubs, analytics, now)
    assert rec.reels_found == 1
    assert rec.winner is not None
    assert rec.winner.publication.publication_id == pubs[0].publication_id


def test_recommendation_reasons_tied_to_metrics(config, object_summary, now):
    pubs = [
        make_reel("1847", "a", published_at=now - timedelta(hours=40)),
        make_reel("1847", "b", published_at=now - timedelta(hours=20)),
    ]
    analytics = {
        pubs[0].publication_id: {
            "reach": 1000, "impressions": 1000, "views": 1000,
            "likes": 10, "comments": 1, "saves": 2, "shares": 1,
        },
        pubs[1].publication_id: {
            "reach": 1000, "impressions": 1000, "views": 1000,
            "likes": 100, "comments": 40, "saves": 90, "shares": 20,
        },
    }
    rec = _run_rank(config, object_summary, pubs, analytics, now)
    text = " ".join(rec.why_selected)
    assert "organic_score" in text
    # Must not invent unsupported causal claims
    assert "бассейн" not in text.lower()
    report = format_report(rec)
    assert "Organic Score" in report
    assert "WAITING FOR APPROVAL" in report


def test_no_fake_analytics(config, object_summary, now):
    pubs = [make_reel("1847", "x", published_at=now - timedelta(hours=10))]
    rec = _run_rank(config, object_summary, pubs, {pubs[0].publication_id: None}, now)
    assert rec.analytics_status == "UNAVAILABLE"
    assert rec.ranking_status == "BLOCKED"
    assert rec.winner is None
    assert rec.ranked == []
    assert rec.reels_found == 1


# ---------------------------------------------------------------------------
# Budget caps
# ---------------------------------------------------------------------------


def test_budget_defaults(config):
    c = suggest_campaign(config.budget)
    assert c.daily_budget == config.budget.default_daily_budget
    assert c.duration_days == config.budget.default_duration_days


def test_budget_max_cap(config):
    c = suggest_campaign(config.budget, daily_budget=999999)
    assert c.daily_budget <= config.budget.max_daily_budget
    assert c.capped is True


def test_duration_cap(config):
    c = suggest_campaign(config.budget, duration_days=999)
    assert c.duration_days <= config.budget.max_duration_days


def test_total_spend_cap(config):
    # Force daily*duration above max_total
    budget = BudgetConfig(
        default_daily_budget=500,
        max_daily_budget=5000,
        default_duration_days=5,
        max_duration_days=30,
        max_total_budget=2500,
    )
    c = suggest_campaign(budget, daily_budget=1000, duration_days=10)
    assert c.daily_budget * c.duration_days <= budget.max_total_budget + 1e-6
    assert c.max_total_budget <= budget.max_total_budget


# ---------------------------------------------------------------------------
# Approval / Meta
# ---------------------------------------------------------------------------


def test_approval_required_and_no_launch_before_approval():
    record = ApprovalRecord(
        approval_id="a1",
        object_id="1847",
        publication_id="pub1",
        organic_score=80,
        daily_budget=500,
        duration_days=5,
        max_total_budget=2500,
        state=ApprovalState.PROPOSED,
        created_at=datetime.now(timezone.utc),
    )
    meta = DisabledMetaAdsAdapter()
    with pytest.raises(ApprovalError):
        # Wrong: try launch without approve — state is PROPOSED
        attempt_launch(record, meta, meta_enabled=False)


def test_meta_disabled_blocks_launch():
    record = ApprovalRecord(
        approval_id="a2",
        object_id="1847",
        publication_id="pub1",
        organic_score=80,
        daily_budget=500,
        duration_days=5,
        max_total_budget=2500,
        state=ApprovalState.PROPOSED,
        created_at=datetime.now(timezone.utc),
    )
    approve(record, approved_by="tester")
    advance_to_ready(record)
    meta = DisabledMetaAdsAdapter()
    with pytest.raises(MetaIntegrationDisabled):
        attempt_launch(record, meta, meta_enabled=False)


def test_mock_meta_has_no_network():
    meta = MockMetaAdsAdapter()
    assert meta.network_calls == 0
    meta.create_campaign(name="x")
    assert meta.network_calls == 0
    assert len(meta.calls) == 1


def test_approval_flow_persisted(config, tmp_data_dir):
    service = PerformanceMarketerService(
        config,
        notion=MockNotionAdapter(
            objects={"1847": ObjectSummary(object_id="1847")},
            publications={},
        ),
        postmypost=MockPostMyPostAdapter(),
    )
    now = datetime.now(timezone.utc)
    pubs = [make_reel("1847", "one", published_at=now - timedelta(hours=12))]
    service.notion.publications["1847"] = pubs  # type: ignore[attr-defined]
    service.postmypost.publications["1847"] = pubs  # type: ignore[attr-defined]
    service.postmypost.analytics[pubs[0].publication_id] = {  # type: ignore[attr-defined]
        "reach": 1000, "impressions": 1000, "views": 2000,
        "likes": 40, "comments": 8, "saves": 25, "shares": 4,
    }
    rec = service.analyze_object("1847", now=now)
    draft = service.create_approval_draft(rec)
    assert draft is not None
    assert draft.state == ApprovalState.PROPOSED
    approved = service.approve_recommendation(draft.approval_id, approved_by="human")
    assert approved.state == ApprovalState.READY_TO_LAUNCH
    assert approved.approved_by == "human"
    with pytest.raises(MetaIntegrationDisabled):
        service.launch(approved.approval_id)


# ---------------------------------------------------------------------------
# Notion / PostMyPost unavailable
# ---------------------------------------------------------------------------


def test_notion_missing_field_handled():
    page = {
        "id": "abc",
        "properties": {
            "Объект ID": {"rich_text": [{"plain_text": "1847"}]},
            # no peer fields, no reel url
        },
    }
    summary = parse_object_page(page)
    assert summary.object_id == "1847"
    assert any(m.startswith("optional:") for m in summary.missing_fields)
    pubs = publications_from_notion_page(page, object_id="1847")
    assert pubs == []


def test_postmypost_unavailable_handled(config):
    stub = StubPostMyPostAdapter()
    assert stub.get_publications("1847") == []
    assert stub.get_post_analytics("x") is None
    assert stub.network_calls == 0

    service = PerformanceMarketerService(
        config,
        notion=MockNotionAdapter(objects={"1847": ObjectSummary(object_id="1847")}),
        postmypost=stub,
    )
    rec = service.analyze_object("1847")
    assert rec.reels_found == 0
    assert rec.meta_launch == "DISABLED"


def test_business_score_model_optional_only():
    m = PaidPerformanceMetrics()
    assert m.spend is None
    assert m.qualified_leads is None
    assert m.reach is None
    assert m.cpm is None
    d = m.to_dict()
    assert d["cpl"] is None
    assert "reach" in d and "cpm" in d


def test_filter_reels_only_instagram():
    pubs = [
        PublicationRecord(
            publication_id="1",
            object_id="1847",
            platform=Platform.INSTAGRAM,
            format=ContentFormat.REEL,
        ),
        PublicationRecord(
            publication_id="2",
            object_id="1847",
            platform=Platform.TIKTOK,
            format=ContentFormat.REEL,
        ),
        PublicationRecord(
            publication_id="3",
            object_id="1847",
            platform=Platform.FACEBOOK,
            format=ContentFormat.POST,
        ),
    ]
    assert len(filter_reels(pubs)) == 1
    assert len(filter_agent10_sources(pubs)) == 2  # IG + FB; TikTok ignored
    assert len(filter_facebook_creatives(pubs)) == 1
    assert all(p.platform.value in {"instagram", "facebook"} for p in filter_agent10_sources(pubs))


# ---------------------------------------------------------------------------
# Isolation: Agent 6/7/8/9 imports unaffected
# ---------------------------------------------------------------------------


def test_agent_6_7_8_9_imports_unaffected():
    """Agent 10 source must not import agent6/7/8/9 packages."""
    src_root = Path(__file__).resolve().parents[1] / "src" / "agent10_marketer"
    forbidden = (
        "agent6_qualifier",
        "agent7_envoy",
        "agent8_notary",
        "agent9_connector",
        "agent_6_qualifier",
        "agent_7_envoy",
        "agent_8_notary",
        "agent7",
        "agent8",
    )
    for path in src_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    assert mod not in forbidden, f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod = node.module.split(".")[0]
                    assert mod not in forbidden, f"{path} imports from {node.module}"
