"""Запись флагов «Монтаж» и «Публикация» в Notion из бота Агента 1.

Кнопки в TG-чате после парсинга решают судьбу объекта:
  Монтаж=ДА  → Агент 3 смонтирует видео (Seedance)
  Монтаж=НЕТ → монтажа нет; при Публикация=ДА цепочка запостит карусель без видео
  Публикация=НЕТ → объект остаётся только в базе (стоп после Агента 2)
Пустой флаг = дефолт из pipeline.json (chain.default_montage / default_publish).
"""
from __future__ import annotations

import json
import os
import urllib.request

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

FIELD_OBJECT_ID = "Объект ID"
FIELD_MONTAGE = "Монтаж"
FIELD_PUBLISH = "Публикация"


def _headers() -> dict:
    key = os.getenv("NOTION_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(url: str, payload: dict | None = None, method: str = "POST") -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def find_page_id(object_id: str) -> str | None:
    db_id = os.getenv("NOTION_DB_ID") or os.getenv("NOTION_DATABASE_ID", "")
    result = _request(
        f"{NOTION_API}/databases/{db_id}/query",
        {
            "filter": {"property": FIELD_OBJECT_ID, "rich_text": {"equals": object_id}},
            "page_size": 1,
        },
    )
    pages = result.get("results", [])
    return pages[0]["id"] if pages else None


def set_flags(object_id: str, montage: str | None = None,
              publish: str | None = None) -> tuple[bool, str]:
    """Записывает выбранные значения флагов (ДА/НЕТ) в строку объекта.

    None — флаг не трогаем (пусто = дефолт цепочки). Возвращает (ok, note).
    """
    if montage is None and publish is None:
        return True, "нечего записывать"
    if not os.getenv("NOTION_API_KEY"):
        return False, "NOTION_API_KEY не задан в .env Агента 1"
    try:
        page_id = find_page_id(object_id)
        if not page_id:
            return False, f"объект {object_id} не найден в Notion"
        props: dict = {}
        if montage is not None:
            props[FIELD_MONTAGE] = {"select": {"name": montage}}
        if publish is not None:
            props[FIELD_PUBLISH] = {"select": {"name": publish}}
        _request(f"{NOTION_API}/pages/{page_id}", {"properties": props}, method="PATCH")
        return True, "ok"
    except Exception as exc:  # сеть/API — не роняем бота из-за флагов
        return False, str(exc)
