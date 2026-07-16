"""Извлечение «Объект ID» (YYYYMMDD_NNN) из сообщений клиента без LLM."""
from __future__ import annotations

import re

# #obj_20260708_001 | #20260708_001 | 20260708_001 | A_20260713_003
# (префикс источника, напр. A_ у Airbnb-парсера, сохраняем в ID;
# служебный obj_ префиксом источника не считается)
_ID_RE = re.compile(r"(?:#obj[_ ]?|#)?\b([A-Za-z]{1,3}_)?(\d{8}_\d{3})\b", re.IGNORECASE)

# Ссылка на пост TG-канала: https://t.me/OpenHome_th/123
_TG_POST_RE = re.compile(r"https?://t\.me/([\w_]+)/(\d+)")


def extract_object_ids(text: str) -> list[str]:
    """Все Объект ID из текста, без дубликатов, в порядке появления.

    Префикс источника (A_20260713_003) сохраняется; служебный obj_ — нет.
    """
    seen: list[str] = []
    for m in _ID_RE.finditer(text or ""):
        prefix = m.group(1) or ""
        if prefix.lower() == "obj_":
            prefix = ""
        oid = prefix + m.group(2)
        if oid not in seen:
            seen.append(oid)
    return seen


def extract_tg_post(text: str) -> tuple[str, int] | None:
    """(channel, message_id) из ссылки на пост TG-канала, если она есть.

    Нужно, когда клиент прислал ссылку на пост без ID в тексте:
    по post_url_telegram находим объект в Notion.
    """
    m = _TG_POST_RE.search(text or "")
    if not m:
        return None
    return m.group(1), int(m.group(2))
