"""Shared Telegram account helpers (session path, safe diagnostics output)."""
from __future__ import annotations

import os
from pathlib import Path

from .runtime_paths import telethon_session_dir


def qualifier_root() -> Path:
    return Path(__file__).resolve().parents[2]


def session_name() -> str:
    return os.environ.get("TG_SESSION", "agent7_userbot").strip() or "agent7_userbot"


def session_file_path() -> Path:
    return telethon_session_dir() / f"{session_name()}.session"


def mask_phone(phone: str | None) -> str:
    raw = (phone or "").strip()
    if not raw:
        return "(not set)"
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def expected_user_id() -> int | None:
    raw = os.environ.get("TG_EXPECTED_USER_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def expected_username() -> str:
    return os.environ.get("TG_EXPECTED_USERNAME", "").strip().lstrip("@").lower()


def identity_check(me) -> str:
    """Return OK / FAIL / SKIP for expected identity env vars."""
    exp_id = expected_user_id()
    exp_user = expected_username()
    if exp_id is None and not exp_user:
        return "SKIP"
    ok = True
    if exp_id is not None and int(getattr(me, "id", 0) or 0) != exp_id:
        ok = False
    if exp_user:
        actual = (getattr(me, "username", None) or "").lower()
        if actual != exp_user:
            ok = False
    return "OK" if ok else "FAIL"
