"""Реестр контактов собственников: data/owners.json.

Agent 8 помечает контакт владельца в момент, когда сам пишет ему первым.
Userbot (Agent 7) перед обработкой входящего сообщения сверяется с реестром:
сообщение от помеченного контакта НИКОГДА не обрабатывается как новый клиент —
даже если активного запроса по объекту сейчас нет.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "data" / "owners.json"


def _load() -> dict:
    if _PATH.exists():
        try:
            return json.loads(_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_PATH)


def _keys(tg_username: str = "", tg_chat_id: str = "") -> list[str]:
    out = []
    u = tg_username.strip().lstrip("@").lower()
    if u:
        out.append(f"tg:{u}")
    if tg_chat_id:
        out.append(f"tg_id:{tg_chat_id}")
    return out


def mark_owner(
    *,
    tg_username: str = "",
    tg_chat_id: str = "",
    object_id: str = "",
    channel: str = "telegram",
) -> None:
    """Помечает контакт как собственника (вызывается при первом исходящем
    сообщении Agent 8 и при первом входящем ответе владельца)."""
    keys = _keys(tg_username, tg_chat_id)
    if not keys:
        return
    data = _load()
    entry = {
        "object_id": object_id,
        "channel": channel,
        "tg_username": tg_username.strip().lstrip("@"),
        "tg_chat_id": tg_chat_id,
        "marked_at": datetime.now().isoformat(timespec="seconds"),
    }
    for k in keys:
        old = data.get(k, {})
        # не затираем object_id, если новый пустой
        if old.get("object_id") and not object_id:
            entry = {**entry, "object_id": old["object_id"]}
        data[k] = entry
    _save(data)


def get_owner(tg_username: str = "", tg_chat_id: str = "") -> dict | None:
    """Запись о собственнике, если контакт помечен; иначе None."""
    data = _load()
    for k in _keys(tg_username, tg_chat_id):
        if k in data:
            return data[k]
    return None
