#!/usr/bin/env python3
"""
Запустить отложенную ChatPlace-воронку для IG Reel, если post_url_instagram_reel уже в Notion.

Вызывается после телефонной публикации (Publisher social) или вручную.

Usage:
  python3 spawn_chatplace_for_reel.py --page-id PAGE_ID
  python3 spawn_chatplace_for_reel.py --page-id PAGE_ID --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys

from publish_pipeline import (
    load_config,
    load_dotenv,
    spawn_chatplace_if_reel_url_ready,
)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="ChatPlace reel funnel after post_url_instagram_reel")
    parser.add_argument("--page-id", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only check readiness, do not spawn deferred job",
    )
    args = parser.parse_args()

    config = load_config()
    if args.dry_run:
        from setup_chatplace_funnel import (
            is_chatplace_kind_done,
            is_live_social_url,
            should_run_chatplace,
        )
        from publish_pipeline import get_prop, notion_get_page

        page = notion_get_page(args.page_id)
        fields = config["notion"]["fields"]
        published = config["notion"]["published_url_fields"]
        reel_field = published.get("instagram_reel", "post_url_instagram_reel")
        reel_url = get_prop(page, reel_field, "url")
        out = {
            "page_id": args.page_id,
            "chatplace_enabled": should_run_chatplace(
                "instagram", config, upload_video=True, mode="video"
            ),
            "reel_url": reel_url,
            "reel_url_live": is_live_social_url(reel_url, "instagram"),
            "reel_funnel_done": is_chatplace_kind_done(page, config, "instagram", "reel"),
            "would_spawn": bool(
                should_run_chatplace("instagram", config, upload_video=True, mode="video")
                and is_live_social_url(reel_url, "instagram")
                and not is_chatplace_kind_done(page, config, "instagram", "reel")
            ),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    spawned = spawn_chatplace_if_reel_url_ready(args.page_id, config)
    if not spawned:
        print(
            json.dumps(
                {
                    "page_id": args.page_id,
                    "spawned": False,
                    "reason": "reel URL missing, funnel done, or chatplace disabled",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps({"page_id": args.page_id, **spawned}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
