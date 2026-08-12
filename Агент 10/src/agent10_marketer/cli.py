"""CLI helpers for Agent 10."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent10_marketer.config import load_config
from agent10_marketer.models import (
    ContentFormat,
    ObjectSummary,
    PeerGroupAttributes,
    Platform,
    PublicationRecord,
)
from agent10_marketer.adapters.notion import MockNotionAdapter
from agent10_marketer.adapters.postmypost import MockPostMyPostAdapter
from agent10_marketer.report import format_cli, format_report
from agent10_marketer.service import PerformanceMarketerService


def _demo_fixture(object_id: str) -> tuple[MockNotionAdapter, MockPostMyPostAdapter]:
    """Offline demo data for CLI when Notion/PostMyPost analytics are unavailable."""
    now = datetime.now(timezone.utc)
    pubs = [
        PublicationRecord(
            publication_id=f"mock:{object_id}:reel:1",
            object_id=object_id,
            platform=Platform.INSTAGRAM,
            format=ContentFormat.REEL,
            instagram_permalink=f"https://www.instagram.com/reel/DEMO1_{object_id}/",
            permalink=f"https://www.instagram.com/reel/DEMO1_{object_id}/",
            published_at=now - timedelta(hours=80),
            source="fixture",
        ),
        PublicationRecord(
            publication_id=f"mock:{object_id}:reel:2",
            object_id=object_id,
            platform=Platform.INSTAGRAM,
            format=ContentFormat.REEL,
            instagram_permalink=f"https://www.instagram.com/reel/DEMO2_{object_id}/",
            permalink=f"https://www.instagram.com/reel/DEMO2_{object_id}/",
            published_at=now - timedelta(hours=30),
            source="fixture",
        ),
        PublicationRecord(
            publication_id=f"mock:{object_id}:reel:3",
            object_id=object_id,
            platform=Platform.INSTAGRAM,
            format=ContentFormat.REEL,
            instagram_permalink=f"https://www.instagram.com/reel/DEMO3_{object_id}/",
            permalink=f"https://www.instagram.com/reel/DEMO3_{object_id}/",
            published_at=now - timedelta(hours=12),
            source="fixture",
        ),
        PublicationRecord(
            publication_id=f"mock:{object_id}:reel:4",
            object_id=object_id,
            platform=Platform.INSTAGRAM,
            format=ContentFormat.REEL,
            instagram_permalink=f"https://www.instagram.com/reel/DEMO4_{object_id}/",
            permalink=f"https://www.instagram.com/reel/DEMO4_{object_id}/",
            published_at=now - timedelta(hours=200),
            source="fixture",
        ),
    ]
    analytics = {
        pubs[0].publication_id: {
            "reach": 4000,
            "impressions": 5200,
            "views": 9000,
            "likes": 220,
            "comments": 18,
            "saves": 95,
            "shares": 12,
        },
        pubs[1].publication_id: {
            "reach": 3500,
            "impressions": 4100,
            "views": 7000,
            "likes": 300,
            "comments": 40,
            "saves": 140,
            "shares": 25,
        },
        pubs[2].publication_id: {
            # High views alone — should NOT force winner without rates/velocity
            "reach": 2000,
            "impressions": 2500,
            "views": 50000,
            "likes": 80,
            "comments": 5,
            "saves": 10,
            "shares": 2,
        },
        pubs[3].publication_id: {
            "reach": 8000,
            "impressions": 9000,
            "views": 12000,
            "likes": 400,
            "comments": 20,
            "saves": 60,
            "shares": 8,
        },
    }
    notion = MockNotionAdapter(
        objects={
            object_id: ObjectSummary(
                object_id=object_id,
                title=f"Demo object {object_id}",
                peer=PeerGroupAttributes(
                    property_type="Condo",
                    district="Bang Tao",
                    rent_type="monthly",
                    price_band="60k_100k",
                    bedrooms=2,
                ),
            )
        },
        publications={object_id: []},
    )
    pmp = MockPostMyPostAdapter(publications={object_id: pubs}, analytics=analytics)
    return notion, pmp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent 10 — Performance Marketer")
    parser.add_argument("object_id", help="Notion Объект ID, e.g. 1847")
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Use offline demo fixture (no live Notion/PostMyPost)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print human-readable Russian report",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override Agent 10 data directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(data_dir=args.data_dir)

    if args.fixture:
        notion, pmp = _demo_fixture(args.object_id)
        service = PerformanceMarketerService(config, notion=notion, postmypost=pmp)
    else:
        service = PerformanceMarketerService(config)

    recommendation = service.analyze_object(args.object_id)
    if recommendation.winner and recommendation.campaign:
        service.create_approval_draft(recommendation)

    text = format_report(recommendation) if args.report else format_cli(recommendation)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
