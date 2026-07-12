#!/usr/bin/env python3
"""
Validate config/publisher.json field names and types against live Notion database.

Usage:
  python3 scripts/verify_notion_schema.py
  python3 scripts/verify_notion_schema.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from publish_pipeline import load_config, load_dotenv  # noqa: E402

NOTION_VERSION = "2022-06-28"

EXPECTED_TYPES: dict[str, str] = {
    "title": "title",
    "object_id": "rich_text",
    "status": "status",
    "photo": "url",
    "description": "rich_text",
    "caption": "rich_text",
    "caption_social": "rich_text",
    "caption_telegram": "rich_text",
    "video_url_Seedance": "url",
    "video_url_vertical": "url",
    "metricool_post_id": "rich_text",
    "agent6_locked": "checkbox",
    "agent6_carousel_done": "checkbox",
    "agent6_video_done": "checkbox",
    "agent6_taken_at": "date",
    "agent6_mode": "select",
    "agent6_log": "rich_text",
    "publish_error": "rich_text",
    "error_count": "number",
}


def fetch_database_properties(database_id: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
        "Notion-Version": NOTION_VERSION,
    }
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data.get("properties", {})


def validate(config: dict[str, Any], props: dict[str, Any]) -> dict[str, Any]:
    fields = config["notion"]["fields"]
    published = config["notion"]["published_url_fields"]
    issues: list[str] = []
    ok: list[str] = []

    for key, column in {**fields, **{f"post_url_{k}": v for k, v in published.items()}}.items():
        if key.startswith("post_url_"):
            expected = "url"
        else:
            expected = EXPECTED_TYPES.get(key)
        if column not in props:
            issues.append(f"MISSING column {column!r} (config key {key})")
            continue
        actual = props[column].get("type")
        if expected and actual != expected:
            issues.append(
                f"TYPE mismatch {column!r}: expected {expected}, got {actual} (key {key})"
            )
        else:
            ok.append(f"{column!r} ({actual})")

    status_field = fields["status"]
    status_prop = props.get(status_field, {})
    status_options = {
        o.get("name")
        for o in (status_prop.get("status") or {}).get("options", [])
        if o.get("name")
    }
    for sk, sv in config["notion"]["statuses"].items():
        if sv not in status_options:
            issues.append(f"STATUS missing in Notion: {sv!r} (config statuses.{sk})")

    live_order = list(props.keys())
    config_order = config.get("notion", {}).get("schema_column_order", [])
    order_mismatch: list[str] = []
    if config_order:
        for i, (live, cfg) in enumerate(zip(live_order, config_order), 1):
            if live != cfg:
                order_mismatch.append(f"#{i}: live={live!r} config={cfg!r}")
        if len(live_order) != len(config_order):
            order_mismatch.append(
                f"column count: live={len(live_order)} config={len(config_order)}"
            )

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "mapped_columns_ok": len(ok),
        "status_options": sorted(status_options),
        "column_order_mismatches": order_mismatch,
        "live_column_count": len(live_order),
    }


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Verify Notion CRM schema vs publisher.json")
    parser.add_argument("--json", action="store_true", help="Print machine-readable report")
    args = parser.parse_args()

    if not os.environ.get("NOTION_API_KEY"):
        print("Set NOTION_API_KEY in .env", file=sys.stderr)
        return 1
    database_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("NOTION_DB_ID")
    if not database_id:
        print("Set NOTION_DATABASE_ID or NOTION_DB_ID in .env", file=sys.stderr)
        return 1

    config = load_config()
    props = fetch_database_properties(database_id)
    report = validate(config, props)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        if report["ok"]:
            print(f"Schema OK — {report['mapped_columns_ok']} mapped columns match Notion.")
        else:
            print("Schema issues:")
            for issue in report["issues"]:
                print(f"  - {issue}")
        if report["column_order_mismatches"]:
            print("\nColumn order drift (update schema_column_order in publisher.json):")
            for line in report["column_order_mismatches"][:10]:
                print(f"  - {line}")
            if len(report["column_order_mismatches"]) > 10:
                print(f"  ... and {len(report['column_order_mismatches']) - 10} more")
        print(f"\nNotion statuses: {', '.join(report['status_options'])}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
