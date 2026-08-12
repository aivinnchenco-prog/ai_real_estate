"""Agent 7 Envoy: реестр контактов собственников — data/owners.json.

Помечает контакт владельца в момент первого исходящего сообщения Envoy.
Userbot (Qualifier) перед обработкой входящего сверяется с реестром:
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


def _normalize_wa_digits(phone: str = "") -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) >= 9:
        digits = "66" + digits[1:]
    return digits


def _keys(
    tg_username: str = "",
    tg_chat_id: str = "",
    whatsapp: str = "",
) -> list[str]:
    out = []
    u = tg_username.strip().lstrip("@").lower()
    if u:
        out.append(f"tg:{u}")
    if tg_chat_id:
        out.append(f"tg_id:{tg_chat_id}")
    wa = _normalize_wa_digits(whatsapp)
    if wa:
        out.append(f"wa:{wa}")
    return out


def mark_owner(
    *,
    tg_username: str = "",
    tg_chat_id: str = "",
    object_id: str = "",
    channel: str = "telegram",
    whatsapp: str = "",
    owner_request_id: str = "",
    client_session_chat_id: str = "",
) -> None:
    """Помечает контакт как собственника (вызывается при первом исходящем
    сообщении Agent 8 и при первом входящем ответе владельца)."""
    keys = _keys(tg_username, tg_chat_id, whatsapp=whatsapp)
    if not keys:
        return
    data = _load()
    entry = {
        "object_id": object_id,
        "channel": channel,
        "tg_username": tg_username.strip().lstrip("@"),
        "tg_chat_id": tg_chat_id,
        "whatsapp": whatsapp.strip(),
        "owner_request_id": owner_request_id,
        "client_session_chat_id": client_session_chat_id,
        "marked_at": datetime.now().isoformat(timespec="seconds"),
    }
    for k in keys:
        old = data.get(k, {})
        # не затираем object_id, если новый пустой
        if old.get("object_id") and not object_id:
            entry = {**entry, "object_id": old["object_id"]}
        if old.get("whatsapp") and not whatsapp:
            entry = {**entry, "whatsapp": old["whatsapp"]}
        if old.get("owner_request_id") and not owner_request_id:
            entry = {**entry, "owner_request_id": old["owner_request_id"]}
        if old.get("client_session_chat_id") and not client_session_chat_id:
            entry = {
                **entry,
                "client_session_chat_id": old["client_session_chat_id"],
            }
        data[k] = entry
    _save(data)


def get_owner(
    tg_username: str = "",
    tg_chat_id: str = "",
    whatsapp: str = "",
) -> dict | None:
    """Запись о собственнике, если контакт помечен; иначе None."""
    data = _load()
    for k in _keys(tg_username, tg_chat_id, whatsapp=whatsapp):
        if k in data:
            return data[k]
    return None
