#!/usr/bin/env python3
"""Clear kWh/deposit figures from «Цена за месяц» on A_20260717_006/005/002."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from listing_parser import parse_listing  # noqa: E402


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if t and not t.startswith("#") and "=" in t:
            k, _, v = t.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


_load_env(ROOT / ".env")
_load_env(ROOT / "agent_6_qualifier" / ".env")

NOTION = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY") or ""
DB = os.environ["NOTION_DATABASE_ID"]
TARGETS = ("A_20260717_006", "A_20260717_005", "A_20260717_002")


def notion(method: str, path: str, body: dict | None = None):
    req = Request(
        f"https://api.notion.com/v1{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {NOTION}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode() or "{}")


def _plain(prop: dict | None) -> str:
    if not prop:
        return ""
    t = prop.get("type")
    if t in ("rich_text", "title"):
        return "".join(x.get("plain_text", "") for x in prop.get(t) or [])
    if t == "number":
        n = prop.get("number")
        return "" if n is None else str(n)
    return ""


def main() -> None:
    for oid in TARGETS:
        data = notion(
            "POST",
            f"/databases/{DB}/query",
            {"filter": {"property": "Объект ID", "rich_text": {"equals": oid}},
             "page_size": 1},
        )
        pages = data.get("results") or []
        if not pages:
            print(f"{oid}: not found")
            continue
        page = pages[0]
        props = page.get("properties") or {}
        desc = _plain(props.get("Описание"))
        old_price = (props.get("Цена за месяц") or {}).get("number")
        old_deposit = (props.get("Залог") or {}).get("number")
        draft = parse_listing(desc, districts=["Rawai", "Laguna", "Thalang", "Layan"])
        patch = {
            "Цена за месяц": {"number": draft.price_monthly},
            "Залог": {"number": draft.deposit},
        }
        notion("PATCH", f"/pages/{page['id']}", {"properties": patch})
        print(
            f"{oid}: price {old_price} -> {draft.price_monthly}; "
            f"deposit {old_deposit} -> {draft.deposit}"
        )


if __name__ == "__main__":
    main()
