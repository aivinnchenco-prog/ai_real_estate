#!/usr/bin/env python3
"""Ensure Agent 9 Notion columns exist (idempotent)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from agent9_connector.notion import FIELD_OWNER_AGENT_TYPE, NotionClient

COLUMN_DEF = {
    FIELD_OWNER_AGENT_TYPE: {
        "select": {
            "options": [
                {"name": "Владелец", "color": "green"},
                {"name": "Агент", "color": "blue"},
            ],
        },
    },
}


def main() -> int:
    nc = NotionClient()
    if not nc.configured:
        print("Notion not configured")
        return 1
    existing = nc._database_property_names()
    missing = {name: spec for name, spec in COLUMN_DEF.items() if name not in existing}
    if not missing:
        print(f"OK: {FIELD_OWNER_AGENT_TYPE} already exists")
        return 0
    nc._req("PATCH", f"/databases/{nc.database_id}", json={"properties": missing})
    nc._property_names = None
    print(f"Created: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
