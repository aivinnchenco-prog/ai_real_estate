#!/usr/bin/env python3
"""
Sync published post URLs from Metricool back to Notion.

Use when posts were scheduled in advance and platform URLs appear only after go-live.
Normally started automatically by publish_pipeline.py (deferred_post_url_sync.py).

Usage:
  python3 sync_post_urls.py --page-id PAGE_ID --platform linkedin
  python3 sync_post_urls.py --page-id PAGE_ID --post-id METRICOOL_POST_ID --platform instagram --post-kind carousel
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from metricool_post_search import resolve_metricool_post_id  # noqa: E402
from publish_pipeline import (  # noqa: E402
    ALL_PUBLISH_PLATFORMS,
    extract_post_url,
    load_config,
    load_dotenv,
    metricool_get_post,
    network_for,
    notion_get_page,
    notion_update_fields,
    notion_url_property,
    postmypost_enabled,
    published_url_field,
)


def infer_instagram_post_kind(post_payload: dict[str, Any]) -> str | None:
    ig = post_payload.get("instagramData") or {}
    post_type = str(ig.get("type") or "").upper()
    if post_type == "REEL":
        return "reel"
    if post_type == "POST":
        return "carousel"
    return None


def sync_post_url_to_notion(
    page_id: str,
    platform: str,
    config: dict[str, Any],
    *,
    post_id: str | None = None,
    post_kind: str | None = None,
    page: dict[str, Any] | None = None,
    post_payload: dict[str, Any] | None = None,
    resolved_source: str | None = None,
) -> dict[str, Any]:
    if postmypost_enabled(config) and post_id:
        from sync_postmypost_urls import sync_postmypost_url_to_notion

        return sync_postmypost_url_to_notion(
            page_id,
            platform,
            config,
            publication_id=post_id,
            post_kind=post_kind,
        )

    page = page or notion_get_page(page_id)
    network = network_for(platform)

    if post_payload is None:
        resolved = resolve_metricool_post_id(
            page_id,
            platform,
            config,
            explicit_post_id=post_id,
            page=page,
            post_kind=post_kind,
        )
        post_id = resolved.get("post_id")
        post_payload = resolved.get("post")
        resolved_source = resolved.get("source")
        if not post_id or not isinstance(post_payload, dict):
            return {
                "page_id": page_id,
                "platform": platform,
                "post_kind": post_kind,
                "post_id": post_id,
                "updated": False,
                "source": resolved_source,
                "error": "metricool_post_not_found",
            }
    elif not post_id:
        post_id = str(post_payload.get("id") or "")

    if network == "instagram" and not post_kind:
        post_kind = infer_instagram_post_kind(post_payload)
    mode = "video" if post_kind == "reel" else "carousel" if post_kind == "carousel" else None
    upload_video = post_kind == "reel"
    url = extract_post_url(post_payload, network)
    url_field = published_url_field(
        platform, config, upload_video=upload_video, mode=mode
    )
    updated = False
    if url and url_field:
        notion_update_fields(page_id, {url_field: notion_url_property(url)})
        updated = True
    return {
        "page_id": page_id,
        "post_id": post_id,
        "platform": platform,
        "post_kind": post_kind,
        "url": url,
        "url_field": url_field,
        "updated": updated,
        "url_type": "published" if url else None,
        "source": resolved_source,
    }


def main() -> int:
    load_dotenv()
    config = load_config()

    parser = argparse.ArgumentParser(description="Sync Metricool post URLs to Notion")
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--post-id", help="Metricool scheduled post ID (optional — auto-search by object_id)")
    parser.add_argument("--platform", help=f"Platform: {', '.join(ALL_PUBLISH_PLATFORMS)}")
    parser.add_argument(
        "--post-kind",
        choices=["carousel", "reel"],
        help="Instagram: carousel vs reel column",
    )
    parser.add_argument("--all-platforms", action="store_true", help="Sync all platforms via object_id search")
    args = parser.parse_args()

    page = notion_get_page(args.page_id)

    platforms = ALL_PUBLISH_PLATFORMS if args.all_platforms else []
    if args.platform:
        platforms = [args.platform]
    if not platforms:
        parser.error("--platform or --all-platforms required")

    updates: dict[str, dict] = {}
    result: dict[str, Any] = {"page_id": args.page_id}

    for platform in platforms:
        one = sync_post_url_to_notion(
            args.page_id,
            platform,
            config,
            post_id=args.post_id,
            post_kind=args.post_kind,
            page=page,
        )
        result[platform] = {
            "url": one.get("url"),
            "post_id": one.get("post_id"),
            "source": one.get("source"),
            "updated": one.get("updated"),
        }
        url_field = one.get("url_field")
        if one.get("url") and url_field:
            updates[url_field] = notion_url_property(one["url"])

    if not updates:
        print(json.dumps({"updated": False, "results": result}, indent=2, ensure_ascii=False))
        print("No platform URLs found yet — post may still be scheduled.", file=sys.stderr)
        return 2

    print(json.dumps({"updated": True, "results": result}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
