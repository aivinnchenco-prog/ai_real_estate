"""Agent 10 read-only Publication Ledger consumer tests."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

AGENT4_SCRIPTS = Path(__file__).resolve().parents[2] / "agent_4_publisher" / "scripts"
sys.path.insert(0, str(AGENT4_SCRIPTS))

from agent10_marketer.adapters.notion import MockNotionAdapter
from agent10_marketer.adapters.postmypost import StubPostMyPostAdapter
from agent10_marketer.adapters.publisher_ledger import PublisherLedgerReader
from agent10_marketer.config import load_config
from agent10_marketer.models import ContentFormat, ObjectSummary, Platform
from agent10_marketer.report import format_cli
from agent10_marketer.service import PerformanceMarketerService
from publication_ledger import PublicationLedger  # noqa: E402


@pytest.fixture
def seeded_ledger(tmp_path: Path) -> Path:
    db = tmp_path / "publications.sqlite3"
    ledger = PublicationLedger(db)
    now = datetime(2026, 8, 8, tzinfo=timezone.utc)
    for i, pid in enumerate(["100", "101", "102"]):
        ledger.register_from_create(
            object_id="1847",
            notion_page_id="page-1847",
            postmypost_publication_id=pid,
            platform="instagram",
            slot="instagram:reel",
            account_id=2214120,
            scheduled_at=(now - timedelta(days=3 - i)).isoformat(),
            post_kind="reel",
            upload_video=True,
            publication_type=4,
            raw_status=5,
        )
    ledger.register_from_create(
        object_id="1847",
        notion_page_id="page-1847",
        postmypost_publication_id="200",
        platform="facebook",
        slot="facebook:post",
        account_id=2214118,
        scheduled_at=now.isoformat(),
        publication_type=1,
        raw_status=5,
    )
    ledger.register_from_create(
        object_id="1847",
        notion_page_id="page-1847",
        postmypost_publication_id="300",
        platform="tiktok",
        slot="tiktok:video",
        account_id=2214123,
        scheduled_at=now.isoformat(),
        upload_video=True,
        publication_type=4,
        raw_status=5,
    )
    return db


def test_agent10_sees_ig_and_fb_ignores_tiktok(seeded_ledger: Path) -> None:
    reader = PublisherLedgerReader(seeded_ledger)
    pubs = reader.get_publications("1847")
    platforms = {p.platform for p in pubs}
    assert Platform.INSTAGRAM in platforms
    assert Platform.FACEBOOK in platforms
    assert Platform.TIKTOK not in platforms


def test_get_instagram_reels_all_historical(seeded_ledger: Path) -> None:
    reader = PublisherLedgerReader(seeded_ledger)
    reels = reader.get_instagram_reels("1847")
    assert len(reels) == 3
    assert all(r.format == ContentFormat.REEL for r in reels)
    assert {r.postmypost_post_id for r in reels} == {"100", "101", "102"}


def test_facebook_creatives_separate(seeded_ledger: Path) -> None:
    reader = PublisherLedgerReader(seeded_ledger)
    fb = reader.get_facebook_creatives("1847")
    assert len(fb) == 1
    assert fb[0].format == ContentFormat.POST


def test_agent10_read_adapter_cannot_mutate(seeded_ledger: Path) -> None:
    reader = PublisherLedgerReader(seeded_ledger)
    with pytest.raises(PermissionError):
        reader.register(object_id="x")
    with pytest.raises(PermissionError):
        reader.write({})


def test_no_analytics_blocks_ranking_shows_history(seeded_ledger: Path, tmp_path: Path) -> None:
    config = load_config(
        config_dir=Path(__file__).resolve().parents[1] / "config",
        data_dir=tmp_path / "a10data",
    )
    service = PerformanceMarketerService(
        config,
        notion=MockNotionAdapter(objects={"1847": ObjectSummary(object_id="1847")}),
        postmypost=StubPostMyPostAdapter(),
        ledger=PublisherLedgerReader(seeded_ledger),
    )
    rec = service.analyze_object("1847")
    assert rec.reels_found == 3
    assert rec.facebook_creatives_found == 1
    assert rec.analytics_status == "UNAVAILABLE"
    assert rec.ranking_status == "BLOCKED"
    assert rec.winner is None
    assert rec.ranked == []
    text = format_cli(rec)
    assert "Instagram Reels: 3" in text
    assert "Facebook creatives: 1" in text
    assert "UNAVAILABLE" in text
    assert "BLOCKED" in text
    assert "100" in text and "101" in text and "102" in text
    assert "300" not in text  # TikTok ignored
