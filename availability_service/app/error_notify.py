"""Telegram error bot (ERROR_BOT_TOKEN / ERROR_CHAT_ID) — как assistant-media/error_notify."""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import resolve_runtime_dir

FACEBOOK_SESSION_LOST_TAG = "availability_facebook_session_lost"
_STATE_FILENAME = "error_notify_state.json"
_COOLDOWN_SEC = int(os.environ.get("ERROR_NOTIFY_COOLDOWN_SEC", "3600"))


def _state_path() -> Path:
    return resolve_runtime_dir() / _STATE_FILENAME


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
    """Отправить сообщение в error-бот. tag — дедупликация (cooldown)."""
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
    except Exception as exc:
        print(f"[availability error_notify] send failed: {exc}")
        return False

    if tag:
        tag_times[tag] = now
        state["tag_times"] = tag_times
        _save_state(state)
    return True


def clear_tag(tag: str) -> None:
    state = _load_state()
    tag_times = state.get("tag_times") or {}
    if tag in tag_times:
        tag_times.pop(tag, None)
        state["tag_times"] = tag_times
        _save_state(state)


def _host_label() -> str:
    return os.environ.get("AVAILABILITY_HOST_LABEL", "").strip() or socket.gethostname()


def _facebook_session_was_lost() -> bool:
    return bool(_load_state().get("facebook_session_lost"))


def _set_facebook_session_lost(active: bool) -> None:
    state = _load_state()
    if active:
        state["facebook_session_lost"] = True
    else:
        state.pop("facebook_session_lost", None)
    _save_state(state)


def maybe_notify_facebook_session_lost(reason: str) -> bool:
    """Сессия Facebook слетела — уведомить один раз (+ cooldown при продолжающейся ошибке)."""
    host = _host_label()
    text = (
        "⚠️ Availability: Facebook сессия слетела\n\n"
        "Checker не может читать Marketplace (LOGIN_REQUIRED / нет c_user).\n"
        "Нужно восстановить сессию:\n"
        "  Mac: agent_1_parser/fb_parser — login в .fb_profile\n"
        "       → export_fb_state.py → import_fb_state.py на VPS\n"
        "Пока не восстановлена, можно временно переключить browser worker на Mac.\n\n"
        f"Host: {host}\n"
        f"Причина: {reason[:500]}"
    )
    sent = notify(text, tag=FACEBOOK_SESSION_LOST_TAG)
    if sent or not _facebook_session_was_lost():
        _set_facebook_session_lost(True)
    return sent


def maybe_notify_facebook_session_restored() -> bool:
    """После успешной проверки — сообщить, что сессия снова работает."""
    if not _facebook_session_was_lost():
        return False
    _set_facebook_session_lost(False)
    clear_tag(FACEBOOK_SESSION_LOST_TAG)
    host = _host_label()
    return notify(
        f"✅ Availability: Facebook сессия восстановлена\n\n"
        f"Checker снова читает Marketplace (ACTIVE/SOLD).\nHost: {host}",
        force=True,
    )


def handle_facebook_check_outcome(outcome: str, status_reason: str = "") -> None:
    """Единая точка для checker / refresh / verify."""
    if outcome == "LOGIN_REQUIRED":
        maybe_notify_facebook_session_lost(status_reason or "redirect_or_login_form")
    elif outcome in ("ACTIVE", "SOLD"):
        maybe_notify_facebook_session_restored()
