#!/usr/bin/env python3
"""Ensure phone FB done checkboxes exist in Notion CRM (idempotent)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from publisher_social.dotenv_util import load_dotenv
from publisher_social import notion_client as notion

COLUMN_DEF = {
    "phone_fb_groups_done": {"checkbox": {}},
    "phone_fb_marketplace_done": {"checkbox": {}},
}


def main() -> int:
    load_dotenv()
    db_id = notion.database_id()
    db = notion.get_database(db_id)
    existing = set((db.get("properties") or {}).keys())
    missing = {name: spec for name, spec in COLUMN_DEF.items() if name not in existing}
    if not missing:
        print("OK: phone_fb_groups_done and phone_fb_marketplace_done already exist")
        return 0
    notion.patch_database(db_id, missing)
    print(f"Created Notion columns: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
