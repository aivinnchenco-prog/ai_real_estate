#!/usr/bin/env python3
"""
Wait until Instagram/TikTok publication + delay, then queue ChatPlace funnel setup.

Spawned from publish_pipeline.py after scheduling a supported post.
"""

from __future__ import annotations

import argparse
import json
import subprocess
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
)
from setup_chatplace_funnel import setup_chatplace_funnel  # noqa: E402


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


def wait_until(target: datetime) -> None:
    now = datetime.now(timezone.utc)
    seconds = (target - now).total_seconds()
    if seconds > 0:
        time.sleep(seconds)


def main() -> int:
    load_dotenv()
    config = load_config()
    cp = config.get("chatplace", {})
    delay = int(cp.get("delay_minutes_after_publish", 15))

    parser = argparse.ArgumentParser(description="Deferred ChatPlace funnel setup")
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--post-id", help="Metricool post ID (optional)")
    parser.add_argument("--post-kind", choices=["carousel", "reel"])
    parser.add_argument("--scheduled-time", help="Publication time ISO UTC")
    parser.add_argument("--delay-minutes", type=int, default=delay)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    scheduled_time = args.scheduled_time
    if not scheduled_time and args.post_id:
        try:
            scheduled_time = scheduled_time_from_metricool_post(args.post_id, config)
        except RuntimeError:
            scheduled_time = None

    if scheduled_time:
        target = parse_scheduled_time_utc(scheduled_time) + timedelta(minutes=args.delay_minutes)
        wait_until(target)

    try:
        result = setup_chatplace_funnel(
            args.page_id,
            args.platform,
            config,
            post_kind=args.post_kind,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "page_id": args.page_id}, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("job_file") or result.get("dry_run") else 2


if __name__ == "__main__":
    sys.exit(main())
