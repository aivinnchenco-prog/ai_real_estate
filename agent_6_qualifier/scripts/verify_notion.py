"""Проверка доступа к Notion и сверка живой схемы с кодом Agent 7/8.

Запуск:  python3 scripts/verify_notion.py [--create-missing]
--create-missing  — создать недостающие availability-колонки (см. AGENT_SPEC.md, раздел 6)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# минимальная загрузка .env без зависимостей
for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip())

from agent7 import notion_store as ns  # noqa: E402

API = "https://api.notion.com/v1"
DB = os.environ["NOTION_DATABASE_ID"]
HEADERS = {
    "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# имя колонки -> (константа в коде, тип Notion для создания)
EXPECTED = {
    ns.PROP_TITLE: "title",
    ns.PROP_OBJECT_ID: "rich_text",
    ns.PROP_DISTRICT: None,
    ns.PROP_TYPE: None,
    ns.PROP_ROOMS: None,
    ns.PROP_PRICE_MONTH: None,
    ns.PROP_PETS: None,
    ns.PROP_PHOTOS: "url",
    ns.PROP_TG_POST: "url",
    ns.PROP_SOURCE: "url",
    ns.PROP_OWNER: None,
    ns.PROP_OWNER_WA: None,
    ns.PROP_OWNER_TG: None,
}

AVAILABILITY_COLUMNS = {
    # «Календарь» — URL, где проверяется доступность дат:
    # Airbnb-объект -> дублируется ссылка объявления; иначе -> ссылка календаря УК;
    # если календаря нет — оставить пустым или написать «ручной» (спрашиваем владельца).
    ns.PROP_CALENDAR: {"url": {}},
    ns.PROP_AVAILABILITY: {
        "select": {"options": [
            {"name": "свободно", "color": "green"},
            {"name": "занято", "color": "red"},
            {"name": "уточняется", "color": "yellow"},
        ]}
    },
    ns.PROP_BUSY_UNTIL: {"date": {}},
    ns.PROP_FUTURE_BOOKINGS: {"rich_text": {}},
    ns.PROP_AVAIL_CHECKED: {"date": {}},
    ns.PROP_FREE_FLAG: {"checkbox": {}},
}


def main() -> int:
    r = requests.get(f"{API}/databases/{DB}", headers=HEADERS, timeout=30)
    if r.status_code != 200:
        print(f"ОШИБКА доступа: HTTP {r.status_code}: {r.json().get('message', r.text)}")
        return 1
    db = r.json()
    title = "".join(t.get("plain_text", "") for t in db.get("title", []))
    props = db["properties"]
    print(f"OK: база «{title}», колонок: {len(props)}")

    print("\n-- Сверка колонок, которые использует код --")
    missing = []
    for name in EXPECTED:
        if name in props:
            print(f"  [ok]  {name}  ({props[name]['type']})")
        else:
            close = [p for p in props if p.strip().lower() == name.strip().lower()]
            hint = f"  <- похоже на: {close}" if close else ""
            print(f"  [НЕТ] {name}{hint}")
            missing.append(name)

    print("\n-- Availability-колонки --")
    to_create = {}
    for name, schema in AVAILABILITY_COLUMNS.items():
        if name in props:
            print(f"  [ok]  {name}  ({props[name]['type']})")
        else:
            print(f"  [нет] {name}")
            to_create[name] = schema

    if to_create and "--create-missing" in sys.argv:
        r = requests.patch(f"{API}/databases/{DB}", headers=HEADERS,
                           json={"properties": to_create}, timeout=30)
        if r.status_code == 200:
            print(f"\nСозданы колонки: {', '.join(to_create)}")
        else:
            print(f"\nОШИБКА создания: HTTP {r.status_code}: {r.json().get('message', r.text)}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
