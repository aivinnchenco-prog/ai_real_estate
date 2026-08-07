#!/usr/bin/env python3
"""
Wait until Metricool publication time + delay, then sync post URL to Notion.

Started automatically by publish_pipeline.py after scheduling a post.
Can also be run manually:

  python3 deferred_post_url_sync.py \\
    --page-id PAGE_ID --platform linkedin \\
    --scheduled-time 2026-07-08T04:52:00.000Z --delay-minutes 5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from publish_pipeline import (  # noqa: E402
    load_config,
    load_dotenv,
    metricool_get_post,
    metricool_timezone,
    parse_scheduled_time_utc,
    postmypost_enabled,
)
from sync_post_urls import sync_post_url_to_notion  # noqa: E402


def scheduled_time_from_metricool_post(post_id: str, config: dict) -> str:
    post = metricool_get_post(post_id)
    pub = post.get("publicationDate") or {}
    date_time = pub.get("dateTime")
    if not date_time:
        raise ValueError(f"Metricool post {post_id} has no publicationDate")
    tz_name = pub.get("timezone") or metricool_timezone(config)
    dt = datetime.fromisoformat(date_time)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def sync_target_utc(scheduled_time: str, delay_minutes: int) -> datetime:
    return parse_scheduled_time_utc(scheduled_time) + timedelta(minutes=delay_minutes)


def wait_until(target: datetime) -> None:
    now = datetime.now(timezone.utc)
    seconds = (target - now).total_seconds()
    if seconds > 0:
        time.sleep(seconds)


def main() -> int:
    load_dotenv()
    config = load_config()

    parser = argparse.ArgumentParser(description="Deferred Metricool post URL sync")
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--post-id", help="Metricool post ID (optional — auto-search by object_id)")
    parser.add_argument("--platform", required=True)
    parser.add_argument(
        "--post-kind",
        choices=["carousel", "reel"],
        help="Instagram: carousel vs reel column",
    )
    parser.add_argument("--scheduled-time", help="ISO schedule time; omit to read from post or skip wait")
    parser.add_argument(
        "--delay-minutes",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=int(config.get("metricool", {}).get("url_sync_retries", 3)),
    )
    parser.add_argument(
        "--retry-interval-minutes",
        type=int,
        default=int(config.get("metricool", {}).get("url_sync_retry_interval_minutes", 3)),
    )
    parser.add_argument(
        "--skip-wait",
        action="store_true",
        help="Sync immediately without waiting for publication time",
    )
    args = parser.parse_args()

    if args.delay_minutes is None:
        if postmypost_enabled(config):
            args.delay_minutes = int(
                (config.get("postmypost") or {}).get("url_sync_delay_minutes", 5)
            )
        else:
            args.delay_minutes = int(
                config.get("metricool", {}).get("url_sync_delay_minutes", 5)
            )

    retries = int(
        (config.get("postmypost") or {}).get("url_sync_retries", 3)
        if postmypost_enabled(config)
        else config.get("metricool", {}).get("url_sync_retries", 3)
    )
    retry_interval = int(
        (config.get("postmypost") or {}).get("url_sync_retry_interval_minutes", 3)
        if postmypost_enabled(config)
        else config.get("metricool", {}).get("url_sync_retry_interval_minutes", 3)
    )
    if args.retries == int(config.get("metricool", {}).get("url_sync_retries", 3)) and postmypost_enabled(config):
        args.retries = retries
    if args.retry_interval_minutes == int(
        config.get("metricool", {}).get("url_sync_retry_interval_minutes", 3)
    ) and postmypost_enabled(config):
        args.retry_interval_minutes = retry_interval

    scheduled_time = args.scheduled_time
    if not args.skip_wait:
        if not scheduled_time and args.post_id and not postmypost_enabled(config):
            try:
                scheduled_time = scheduled_time_from_metricool_post(args.post_id, config)
            except (RuntimeError, ValueError):
                scheduled_time = None
        if scheduled_time:
            target = sync_target_utc(scheduled_time, args.delay_minutes)
            wait_until(target)

    last_result: dict | None = None
    attempts = max(1, args.retries)
    for attempt in range(attempts):
        last_result = sync_post_url_to_notion(
            args.page_id,
            args.platform,
            config,
            post_id=args.post_id,
            post_kind=args.post_kind,
        )
        last_result["attempt"] = attempt + 1
        last_result["sync_at_utc"] = datetime.now(timezone.utc).isoformat()
        if last_result.get("updated"):
            print(json.dumps(last_result, indent=2, ensure_ascii=False))
            return 0
        if last_result.get("reason") in {"noop_same", "conflict"}:
            print(json.dumps(last_result, indent=2, ensure_ascii=False))
            return 0
        if attempt + 1 < attempts:
            time.sleep(max(1, args.retry_interval_minutes) * 60)

    print(json.dumps(last_result or {}, indent=2, ensure_ascii=False))
    backend = "PostMyPost" if postmypost_enabled(config) else "Metricool"
    print(f"Post URL not available yet in {backend}.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
