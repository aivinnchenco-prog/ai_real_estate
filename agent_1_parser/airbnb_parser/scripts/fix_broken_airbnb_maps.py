#!/usr/bin/env python3
"""One-shot: re-fetch Airbnb coords for known broken objects and patch Notion Maps.

Run on VPS:
  cd /opt/openhome/app/agent_1_parser/airbnb_parser
  /opt/openhome/venv/bin/python scripts/fix_broken_airbnb_maps.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from airbnb_parser import AirbnbParser  # noqa: E402
from agent2_handoff import find_agent2_root  # noqa: E402

BROKEN = [
    {
        "object_id": "A_20260819_001",
        "page_id": "3c12c251-5061-81ba-bcc2-e40a6ef9cf3a",
        "session": "A_20260819_094900",
        "url": "https://www.airbnb.ru/rooms/53465355?currency=THB",
    },
    {
        "object_id": "A_20260819_002",
        "page_id": "3c12c251-5061-811d-a331-d678f68ba4ac",
        "session": "A_20260819_153328",
        "url": "https://www.airbnb.ru/rooms/48188020?currency=THB",
    },
    {
        "object_id": "A_20260819_003",
        "page_id": "3c12c251-5061-8115-81b0-d3bfcee6a4a2",
        "session": "A_20260819_161953",
        "url": "https://www.airbnb.ru/rooms/1314090649319364414?currency=THB",
    },
    {
        "object_id": "A_20260819_004",
        "page_id": "3c12c251-5061-8131-9bf7-d7753f24ffc3",
        "session": "A_20260819_163901",
        "url": "https://www.airbnb.ru/rooms/1622955445362144845?currency=THB",
    },
    {
        "object_id": "A_20260820_001",
        "page_id": "3c22c251-5061-81fb-99a2-f52238228e05",
        "session": "A_20260820_122038",
        "url": "https://www.airbnb.ru/rooms/1689071418914331004?currency=THB",
    },
    {
        "object_id": "A_20260820_002",
        "page_id": "3c22c251-5061-8199-8494-e355e197953a",
        "session": "A_20260820_122546",
        "url": "https://www.airbnb.ru/rooms/1222945528733785855?currency=THB",
    },
]


def _load_env() -> None:
    for p in (Path("/opt/openhome/.env"), ROOT / ".env"):
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_env()
    agent2 = find_agent2_root()
    if not agent2:
        print("FAIL: agent2 root not found")
        return 1
    sys.path.insert(0, str(agent2 / "scripts"))
    sys.path.insert(0, str(agent2))
    from maps_resolver import resolve_google_maps
    from real_estate_handler import NotionCRM

    crm = NotionCRM(
        os.environ["NOTION_API_KEY"],
        os.environ.get("NOTION_DB_ID") or os.environ["NOTION_DATABASE_ID"],
    )

    parser = AirbnbParser(headless=True)
    ok_n = 0
    try:
        for job in BROKEN:
            oid = job["object_id"]
            print(f"\n=== {oid} ===", flush=True)
            try:
                _msg, _media, data = parser.process_url(job["url"])
            except Exception as exc:
                print(f"  parse error: {exc}")
                continue
            loc = data.get("Локация") or {}
            lat, lng = loc.get("latitude"), loc.get("longitude")
            if lat is None or lng is None:
                print("  STILL NO COORDS")
                continue
            print(f"  coords {lat}, {lng} subtitle={loc.get('subtitle')!r}")

            session = agent2 / "data" / "sessions" / job["session"]
            desc = ""
            if (session / "description.txt").exists():
                desc = (session / "description.txt").read_text(encoding="utf-8")
            title = desc.splitlines()[0] if desc.strip() else (data.get("Название") or oid)
            maps = resolve_google_maps(
                desc or title,
                "Phuket",
                title,
                coords=(float(lat), float(lng)),
            )
            print(f"  maps method={maps.method} district={maps.district}")
            print(f"  url={maps.url}")

            if (session / "parsed.json").exists():
                meta = json.loads((session / "parsed.json").read_text(encoding="utf-8"))
                meta["location"] = {
                    "latitude": float(lat),
                    "longitude": float(lng),
                    "subtitle": loc.get("subtitle") or "",
                }
                (session / "parsed.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            props = {
                "Google Maps": NotionCRM.build_url(maps.url),
                "Район": NotionCRM.build_text(maps.district),
                "Адрес": NotionCRM.build_text(
                    (loc.get("subtitle") or maps.address or "").strip()
                ),
            }
            crm.update_page(job["page_id"], props)
            print("  Notion updated")
            ok_n += 1
    finally:
        try:
            parser.close()
        except Exception:
            pass

    print(f"\nDone: {ok_n}/{len(BROKEN)} fixed")
    return 0 if ok_n == len(BROKEN) else 2


if __name__ == "__main__":
    raise SystemExit(main())
