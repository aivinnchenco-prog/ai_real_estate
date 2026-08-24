"""Telegram alerts for Agent 9 (ERROR_BOT_TOKEN / ERROR_CHAT_ID)."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config_loader import data_dir

_COOLDOWN_SEC = int(os.environ.get("AGENT9_ERROR_NOTIFY_COOLDOWN_SEC", "3600"))
_STATE_FILE = "error_notify_state.json"

TAG_LOGIN_REQUIRED = "agent9_login_required"
TAG_CHECKPOINT = "agent9_checkpoint"
TAG_ACCOUNT_PAUSED = "agent9_account_paused"


def _state_path() -> Path:
    return data_dir() / _STATE_FILE


def _load_state() -> dict:
    path = _state_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def notify(text: str, *, tag: str = "", force: bool = False) -> bool:
    token = os.environ.get("ERROR_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("ERROR_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    state = _load_state()
    now = time.time()
    tag_times = state.get("tag_times") or {}
    if tag and not force:
        last = float(tag_times.get(tag, 0))
        if now - last < _COOLDOWN_SEC:
            return False

    host = socket.gethostname()
    body = f"[Agent9 @ {host}]\n{text}"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": body[:4000],
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
    except Exception as exc:
        print(f"[agent9 error_notify] send failed: {exc}")
        return False

    if tag:
        tag_times[tag] = now
        state["tag_times"] = tag_times
        _save_state(state)
    return True


def alert_facebook_security(screen: str, *, object_id: str = "") -> None:
    mapping = {
        "LOGIN_REQUIRED": TAG_LOGIN_REQUIRED,
        "CHECKPOINT": TAG_CHECKPOINT,
        "ACCOUNT_PAUSED": TAG_ACCOUNT_PAUSED,
    }
    tag = mapping.get(screen)
    if not tag:
        return
    notify(
        f"Facebook {screen} during outreach (object_id={object_id or 'n/a'}). "
        "Run facebook_login or restore agent9 profile session.",
        tag=tag,
    )
