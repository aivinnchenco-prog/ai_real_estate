#!/usr/bin/env python3
"""Add missing Notion CRM columns for Agent 6 Publisher."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

NOTION_VERSION = "2022-06-28"

PUBLISHED_URL_COLUMNS = [
    "post_url_instagram_carousel",
    "post_url_instagram_reel",
    "post_url_tiktok",
    "post_url_x",
    "post_url_linkedin",
    "post_url_facebook",
    "post_url_youtube",
    "post_url_threads",
    "post_url_telegram",
]

AGENT6_COLUMNS = {
    "agent6_locked": {"checkbox": {}},
    "agent6_carousel_done": {"checkbox": {}},
    "agent6_video_done": {"checkbox": {}},
    "agent6_taken_at": {"date": {}},
    "chatplace_funnel_done": {"checkbox": {}},
    "chatplace_funnel_id": {"rich_text": {}},
    "chatplace_funnel_carousel_done": {"checkbox": {}},
    "chatplace_funnel_reel_done": {"checkbox": {}},
    "chatplace_funnel_carousel_id": {"rich_text": {}},
    "chatplace_funnel_reel_id": {"rich_text": {}},
    "CTA Instagram": {"rich_text": {}},
    "agent6_mode": {
        "select": {
            "options": [
                {"name": "auto", "color": "blue"},
                {"name": "manual", "color": "yellow"},
                {"name": "force", "color": "red"},
            ]
        }
    },
    "agent6_log": {"rich_text": {}},
}

COLUMNS = {
    "metricool_post_id": {"rich_text": {}},
    "publora_post_group_id": {"rich_text": {}},
    "Описание сец.сети": {"rich_text": {}},
    **{name: {"url": {}} for name in PUBLISHED_URL_COLUMNS},
    **AGENT6_COLUMNS,
}

DEFAULT_COLUMNS = ["metricool_post_id", *PUBLISHED_URL_COLUMNS, *AGENT6_COLUMNS.keys()]


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_dotenv() -> None:
    root = package_root()
    for name in (".env", ".env.local"):
        env_path = root / name
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
        break

    monorepo_env = root.parent / "agent_2_registrar" / "_import" / "assistant-media" / ".env.real-estate"
    if monorepo_env.exists():
        for line in monorepo_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def notion_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def get_database(database_id: str) -> dict:
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=notion_headers(),
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def add_columns(database_id: str, columns: dict[str, dict], dry_run: bool) -> list[str]:
    db = get_database(database_id)
    existing = set(db.get("properties", {}))
    to_add = {name: spec for name, spec in columns.items() if name not in existing}

    if not to_add:
        return []

    if dry_run:
        return list(to_add)

    payload = json.dumps({"properties": to_add}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{database_id}",
        data=payload,
        method="PATCH",
        headers=notion_headers(),
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        json.loads(resp.read())
    return list(to_add)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Add Notion CRM columns for Publisher")
    parser.add_argument(
        "--column",
        action="append",
        help="Column name (default: metricool_post_id + all post_url_* columns)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    database_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("NOTION_DB_ID")
    if not os.environ.get("NOTION_API_KEY") or not database_id:
        print("Set NOTION_API_KEY and NOTION_DATABASE_ID in .env", file=sys.stderr)
        return 1

    names = args.column or DEFAULT_COLUMNS
    columns = {name: COLUMNS.get(name, {"rich_text": {}}) for name in names}

    try:
        added = add_columns(database_id, columns, args.dry_run)
    except urllib.error.HTTPError as e:
        print(f"Notion API error {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    if not added:
        print("Nothing to add — all columns already exist.")
        return 0

    if args.dry_run:
        print("Would add:", ", ".join(added))
    else:
        print("Added:", ", ".join(added))
    return 0


if __name__ == "__main__":
    sys.exit(main())
