"""One-shot: add «URL объекта календаря» to target Notion DB if missing."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

COL = "URL объекта календаря"


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    import sys
    sys.path.insert(0, str(root))
    from availability_service.app.config import load_config

    config = load_config()
    token = config.notion_api_key
    db_id = config.target_database_id
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
    }
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        schema = json.loads(resp.read())
    if COL in (schema.get("properties") or {}):
        print(f"{COL}: already exists")
        return

    payload = {"properties": {COL: {"url": {}}}}
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{db_id}",
        data=json.dumps(payload).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read())
    print(f"{COL}: created")
    print("properties count:", len(out.get("properties") or {}))


if __name__ == "__main__":
    main()
