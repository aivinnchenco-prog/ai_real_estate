#!/usr/bin/env python3
"""
Catch-up pass: replace leftover PostMyPost planner links in Notion with live permalinks.

The deferred sync only watches a post for ~1 hour after its slot. If PostMyPost
published later (or the sync process died), the planner link stays in Notion
forever — nothing else retries it. Run this periodically to close that gap.

Also clears dead TikTok links (tiktok.com/@/video/v_pub_url~…) that an earlier
column-routing bug wrote into post_url_tiktok_carousel: PostMyPost never resolves
those into a real permalink, so they are unopenable.

Usage:
  python3 backfill_planner_urls.py                       # dry-run over whole CRM
  python3 backfill_planner_urls.py --apply
  python3 backfill_planner_urls.py --object-id A_20260817_001 --apply
  python3 backfill_planner_urls.py --clean-dead-tiktok --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from postmypost_client import extract_publication_url, postmypost_get_publication  # noqa: E402
from postmypost_social_url import (  # noqa: E402
    decide_notion_url_update,
    is_postmypost_planner_url,
    is_unresolved_tiktok_url,
    tiktok_url_matches_mode,
)
from publish_pipeline import (  # noqa: E402
    load_config,
    load_dotenv,
    network_for,
    notion_headers,
    notion_update_fields,
    notion_url_property,
    postmypost_enabled,
    req,
)

# published_url_fields key → (platform, mode for published_url_field)
SLOT_BY_FIELD_KEY: dict[str, tuple[str, str | None]] = {
    "instagram_carousel": ("instagram", "carousel"),
    "instagram_reel": ("instagram", "video"),
    "tiktok": ("tiktok", "video"),
    "tiktok_carousel": ("tiktok", "carousel"),
    "x": ("x", None),
    "linkedin": ("linkedin", None),
    "facebook": ("facebook", None),
    "youtube": ("youtube", None),
    "threads": ("threads", None),
}

_SHARE_ID_RE = re.compile(r"#share=(\d+)")
_PUBLICATION_ID_RE = re.compile(r"/publications/(\d+)")


def publication_id_from_url(url: str) -> str | None:
    """Publication id from either planner form: /publications/{id} or #share={id}."""
    for pattern in (_PUBLICATION_ID_RE, _SHARE_ID_RE):
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return None


def is_planner_link(url: str) -> bool:
    if is_postmypost_planner_url(url):
        return True
    return "postmypost.io" in (url or "").lower() and bool(publication_id_from_url(url))


def notion_query_all(database_id: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = req(
            "POST",
            f"https://api.notion.com/v1/databases/{database_id}/query",
            notion_headers(),
            json.dumps(body).encode("utf-8"),
        )
        pages.extend(result.get("results", []))
        if not result.get("has_more"):
            return pages
        cursor = result.get("next_cursor")


def object_id_of(page: dict[str, Any], fields: dict[str, str]) -> str:
    prop = (page.get("properties") or {}).get(fields.get("object_id", "Объект ID")) or {}
    return "".join(x.get("plain_text", "") for x in prop.get("rich_text") or [])


def planner_slots(page: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Slots of this page whose Notion column still holds a planner link."""
    mapping = config.get("notion", {}).get("published_url_fields", {})
    props = page.get("properties") or {}
    slots: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for key, (platform, mode) in SLOT_BY_FIELD_KEY.items():
        field = mapping.get(key)
        if not field or field in seen_fields:
            continue
        prop = props.get(field) or {}
        if prop.get("type") != "url":
            continue
        url = (prop.get("url") or "").strip()
        if not url or not is_planner_link(url):
            continue
        publication_id = publication_id_from_url(url)
        if not publication_id:
            continue
        seen_fields.add(field)
        slots.append(
            {
                "field": field,
                "platform": platform,
                "mode": mode,
                "current_url": url,
                "publication_id": publication_id,
            }
        )
    return slots


def dead_tiktok_slots(page: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Columns holding an unresolvable TikTok link written by the routing bug."""
    mapping = config.get("notion", {}).get("published_url_fields", {})
    props = page.get("properties") or {}
    slots: list[dict[str, Any]] = []
    for key in ("tiktok", "tiktok_carousel"):
        field = mapping.get(key)
        if not field:
            continue
        prop = props.get(field) or {}
        if prop.get("type") != "url":
            continue
        url = (prop.get("url") or "").strip()
        if url and is_unresolved_tiktok_url(url):
            slots.append({"field": field, "current_url": url})
    return slots


def resolve_slot(slot: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    platform = slot["platform"]
    network = network_for(platform)
    try:
        payload = postmypost_get_publication(slot["publication_id"], config)
    except RuntimeError as exc:
        return {**slot, "updated": False, "reason": "api_error", "error": str(exc)[:300]}

    url = extract_publication_url(payload, platform, config)
    should_update, url_to_write, reason = decide_notion_url_update(
        slot["current_url"], url, network, mode=slot["mode"]
    )
    raw_url = next(
        (
            post.get("url")
            for post in payload.get("posts") or []
            if isinstance(post, dict) and post.get("url")
        ),
        None,
    )
    if not url and is_unresolved_tiktok_url(raw_url):
        reason = "tiktok_permalink_unresolved"
    elif url and not should_update and reason == "invalid_new":
        # Ссылка живая, но формат не тот, что у колонки: чужой слот не трогаем.
        if network == "tiktok" and not tiktok_url_matches_mode(url, slot["mode"]):
            reason = "tiktok_format_mismatch"
    return {
        **slot,
        "publication_status": payload.get("publication_status"),
        "live_url": url,
        "raw_url": raw_url,
        "should_update": should_update,
        "url_to_write": url_to_write,
        "reason": reason,
    }


def main() -> int:
    load_dotenv()
    config = load_config()
    if not postmypost_enabled(config):
        print(json.dumps({"skipped": True, "reason": "postmypost_disabled"}))
        return 0

    parser = argparse.ArgumentParser(description="Backfill live URLs over PostMyPost planner links")
    parser.add_argument("--apply", action="store_true", help="Write to Notion (default: dry-run)")
    parser.add_argument("--object-id", help="Limit to one CRM object")
    parser.add_argument("--page-id", help="Limit to one Notion page")
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.0,
        help="Pause between PostMyPost calls",
    )
    parser.add_argument(
        "--clean-dead-tiktok",
        action="store_true",
        help="Also clear unopenable tiktok.com/@/video/v_pub_url~… links",
    )
    args = parser.parse_args()

    fields = config["notion"]["fields"]
    if args.page_id:
        from publish_pipeline import notion_get_page

        pages = [notion_get_page(args.page_id)]
    else:
        database_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("NOTION_DB_ID")
        if not database_id:
            print("Set NOTION_DATABASE_ID or NOTION_DB_ID in .env", file=sys.stderr)
            return 1
        pages = notion_query_all(database_id)

    report: list[dict[str, Any]] = []
    cleaned: list[dict[str, Any]] = []
    updated_count = 0
    for page in pages:
        object_id = object_id_of(page, fields)
        if args.object_id and object_id != args.object_id:
            continue
        page_id = page.get("id", "")
        for slot in planner_slots(page, config):
            resolved = resolve_slot(slot, config)
            time.sleep(max(0.0, args.sleep_seconds))
            if args.apply and resolved.get("should_update") and resolved.get("url_to_write"):
                notion_update_fields(
                    page_id,
                    {resolved["field"]: notion_url_property(resolved["url_to_write"])},
                )
                resolved["updated"] = True
                updated_count += 1
            else:
                resolved["updated"] = False
            report.append({"object_id": object_id, "page_id": page_id, **resolved})

        if not args.clean_dead_tiktok:
            continue
        for slot in dead_tiktok_slots(page, config):
            if args.apply:
                notion_update_fields(page_id, {slot["field"]: {"url": None}})
            cleaned.append(
                {
                    "object_id": object_id,
                    "page_id": page_id,
                    **slot,
                    "cleared": args.apply,
                }
            )

    print(
        json.dumps(
            {
                "apply": args.apply,
                "planner_slots": len(report),
                "updated": updated_count,
                "dead_tiktok_slots": len(cleaned),
                "slots": report,
                "dead_tiktok": cleaned,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
