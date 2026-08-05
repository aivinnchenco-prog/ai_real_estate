#!/usr/bin/env python3
"""Deferred PostMyPost IG automation setup after publication."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from publish_pipeline import load_config, load_dotenv, parse_scheduled_time_utc  # noqa: E402
from setup_postmypost_funnel import setup_postmypost_funnel  # noqa: E402


def main() -> int:
    load_dotenv()
    config = load_config()
    auto = (config.get("postmypost") or {}).get("automation") or {}

    parser = argparse.ArgumentParser(description="Deferred PostMyPost funnel setup")
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--platform", default="instagram")
    parser.add_argument("--post-kind", choices=["carousel", "reel"])
    parser.add_argument("--scheduled-time")
    parser.add_argument(
        "--delay-minutes",
        type=int,
        default=int(auto.get("delay_minutes_after_publish", 15)),
    )
    args = parser.parse_args()

    if args.scheduled_time:
        target = parse_scheduled_time_utc(args.scheduled_time) + timedelta(
            minutes=args.delay_minutes
        )
        wait = (target - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            time.sleep(wait)

    result = setup_postmypost_funnel(
        args.page_id,
        args.platform,
        config,
        post_kind=args.post_kind,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") or result.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
