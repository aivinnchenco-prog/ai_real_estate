#!/usr/bin/env python3
"""Уведомления об ошибках пайплайна в Telegram (@Error_real_estate_bot).

Использует ERROR_BOT_TOKEN / ERROR_CHAT_ID из окружения (.env корня проекта).
Дедупликация: одинаковый tag не шлётся чаще, чем раз в NOTIFY_COOLDOWN_SEC.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "data" / "error_notify_state.json"
NOTIFY_COOLDOWN_SEC = int(os.environ.get("ERROR_NOTIFY_COOLDOWN_SEC", "3600"))


def _load_state() -> dict:
    try:
        with STATE_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def notify(text: str, *, tag: str = "", force: bool = False) -> bool:
    """Отправить сообщение в error-бот. Возвращает True, если отправлено.

    tag — ключ дедупликации (например "higgsfield_auth"): одинаковые
    уведомления не спамят чаще NOTIFY_COOLDOWN_SEC. force=True шлёт всегда.
    """
    token = os.environ.get("ERROR_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("ERROR_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    state = _load_state()
    now = time.time()
    if tag and not force:
        last = state.get(tag, 0)
        if now - last < NOTIFY_COOLDOWN_SEC:
            return False

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": "true",
    }).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
            ),
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001 — уведомления не должны ронять цепочку
        print(f"[error_notify] send failed: {exc}")
        return False

    if tag:
        state[tag] = now
        _save_state(state)
    return True


def clear_tag(tag: str) -> None:
    """Сбросить дедупликацию (например, после восстановления авторизации)."""
    state = _load_state()
    if tag in state:
        state.pop(tag)
        _save_state(state)


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or "test: error_notify работает"
    ok = notify(msg, force=True)
    print("sent" if ok else "not sent (нет ERROR_BOT_TOKEN/ERROR_CHAT_ID?)")
