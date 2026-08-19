#!/usr/bin/env python3
"""Sync PostMyPost publication URLs and analytics to Notion."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from postmypost_client import extract_publication_url, postmypost_get_publication  # noqa: E402
from postmypost_social_url import decide_notion_url_update  # noqa: E402
from publish_pipeline import (  # noqa: E402
    load_config,
    load_dotenv,
    network_for,
    notion_get_page,
    notion_update_fields,
    notion_url_property,
    post_kind_slot_flags,
    postmypost_enabled,
    published_url_field,
)


def _notion_url_from_page(page: dict[str, Any], field_name: str | None) -> str:
    if not field_name:
        return ""
    prop = (page.get("properties") or {}).get(field_name) or {}
    if prop.get("type") == "url":
        return (prop.get("url") or "").strip()
    return ""


def sync_postmypost_url_to_notion(
    page_id: str,
    platform: str,
    config: dict[str, Any],
    *,
    publication_id: str,
    post_kind: str | None = None,
    page: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not publication_id:
        return {"updated": False, "error": "missing_publication_id"}

    payload = postmypost_get_publication(publication_id, config)
    network = network_for(platform)
    url = extract_publication_url(payload, platform, config)

    upload_video, mode = post_kind_slot_flags(post_kind)
    url_field = published_url_field(
        platform, config, upload_video=upload_video, mode=mode
    )

    page = page or notion_get_page(page_id)
    current_url = _notion_url_from_page(page, url_field)
    should_update, url_to_write, reason = decide_notion_url_update(
        current_url, url, network, mode=mode
    )

    updated = False
    if should_update and url_field and url_to_write:
        notion_update_fields(page_id, {url_field: notion_url_property(url_to_write)})
        updated = True

    # Enrich canonical ledger (same PostMyPost id — no duplicate insert).
    ledger_info: dict[str, Any] = {"enriched": False}
    try:
        import publication_ledger as pl
        from postmypost_social_url import is_valid_social_post_url

        social_permalink = url if url and is_valid_social_post_url(url, network) else None
        enriched = pl.enrich_publication_in_ledger(
            publication_id,
            payload,
            permalink=social_permalink,
            platform=network,
        )
        if enriched is not None:
            ledger_info = {
                "enriched": True,
                "publication_key": enriched.publication_key,
                "permalink": enriched.permalink,
                "external_media_id": enriched.external_media_id,
                "status": enriched.status,
                "raw_status": enriched.raw_status,
            }
    except Exception as exc:  # noqa: BLE001
        ledger_info = {"enriched": False, "error": str(exc)[:300]}

    return {
        "page_id": page_id,
        "platform": platform,
        "publication_id": publication_id,
        "post_kind": post_kind,
        "url": url,
        "url_field": url_field,
        "updated": updated,
        "reason": reason,
        "publication_status": payload.get("publication_status"),
        "ledger": ledger_info,
    }


def sync_postmypost_analytics(
    page_id: str,
    platform: str,
    config: dict[str, Any],
    *,
    publication_id: str,
) -> dict[str, Any]:
    """Заглушка под GET /analytics/publications — сохраняем в agent6_log."""
    from postmypost_client import postmypost_request, resolve_account_ids

    project_id = int((config.get("postmypost") or {}).get("project_id") or 0)
    account_ids = resolve_account_ids(platform, config)
    if not project_id or not account_ids:
        return {"skipped": True, "reason": "missing_project_or_account"}

    today = datetime.now(timezone.utc).date()
    week_ago = today - timedelta(days=7)
    query = (
        f"project_id={project_id}"
        f"&account_id={account_ids[0]}"
        f"&date_from={week_ago.isoformat()}"
        f"&date_to={today.isoformat()}"
    )
    try:
        data = postmypost_request("GET", f"/analytics/publications?{query}", config=config)
    except RuntimeError as exc:
        return {"skipped": True, "error": str(exc)[:300]}

    fields = config["notion"]["fields"]
    log_field = fields.get("agent6_log", "agent6_log")
    summary = json.dumps(data, ensure_ascii=False)[:1800]
    notion_update_fields(
        page_id,
        {log_field: {"rich_text": [{"text": {"content": f"pmp analytics {platform}: {summary}"}}]}},
    )
    return {"ok": True, "platform": platform, "publication_id": publication_id}


def main() -> int:
    load_dotenv()
    config = load_config()
    if not postmypost_enabled(config):
        print(json.dumps({"skipped": True, "reason": "postmypost_disabled"}))
        return 0

    parser = argparse.ArgumentParser(description="Sync PostMyPost URLs/analytics")
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--publication-id", required=True)
    parser.add_argument("--post-kind", choices=["carousel", "reel", "video"])
    parser.add_argument("--analytics", action="store_true")
    args = parser.parse_args()

    page = notion_get_page(args.page_id)
    result = sync_postmypost_url_to_notion(
        args.page_id,
        args.platform,
        config,
        publication_id=args.publication_id,
        post_kind=args.post_kind,
        page=page,
    )
    if args.analytics:
        result["analytics"] = sync_postmypost_analytics(
            args.page_id,
            args.platform,
            config,
            publication_id=args.publication_id,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("updated"):
        return 0
    if result.get("reason") in {"noop_same", "conflict"}:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
