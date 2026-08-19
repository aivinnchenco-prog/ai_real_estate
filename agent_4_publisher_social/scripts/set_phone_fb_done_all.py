#!/usr/bin/env python3
"""Set phone_fb_groups_done and phone_fb_marketplace_done on all CRM rows."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from publisher_social.dotenv_util import load_dotenv
from publisher_social import notion_client as notion

FIELDS = ("phone_fb_groups_done", "phone_fb_marketplace_done")


def iter_all_pages(database_id: str) -> list[dict]:
    pages: list[dict] = []
    cursor: str | None = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion.req(
            "POST",
            f"https://api.notion.com/v1/databases/{database_id}/query",
            json.dumps(body).encode("utf-8"),
        )
        pages.extend(result.get("results") or [])
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return pages


def needs_update(page: dict) -> bool:
    props = page.get("properties") or {}
    for name in FIELDS:
        prop = props.get(name) or {}
        if prop.get("type") != "checkbox" or not prop.get("checkbox"):
            return True
    return False


def main() -> int:
    load_dotenv()
    db_id = notion.database_id()
    pages = iter_all_pages(db_id)
    print(f"Found {len(pages)} pages in database")
    updated = 0
    skipped = 0
    for page in pages:
        page_id = page["id"]
        if not needs_update(page):
            skipped += 1
            continue
        notion.update_fields(
            page_id,
            {name: notion.checkbox_prop(True) for name in FIELDS},
        )
        updated += 1
        title_prop = (page.get("properties") or {}).get("Название объекта") or {}
        title_items = title_prop.get("title") or []
        title = "".join(t.get("plain_text", "") for t in title_items) or page_id
        print(f"  [{updated}] {title}")
        time.sleep(0.35)
    print(f"Done: updated={updated}, already_set={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
