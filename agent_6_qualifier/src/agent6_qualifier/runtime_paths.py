"""Production-safe persistent paths.

Env overrides win. Without env, paths stay app-relative so local tests
and developer checkouts keep working.
"""
from __future__ import annotations

import os
from pathlib import Path


def _qualifier_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _env_path(*names: str) -> Path | None:
    for name in names:
        raw = (os.getenv(name) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def telethon_session_dir() -> Path:
    return _env_path("TG_SESSION_DIR", "OPENHOME_SESSIONS_DIR") or _qualifier_root()


def qualifier_session_store_dir() -> Path:
    return (
        _env_path("AGENT6_SESSION_STORE_DIR")
        or _qualifier_root() / "data" / "sessions"
    )


def contracts_dir() -> Path:
    return (
        _env_path("AGENT8_CONTRACTS_DIR", "OPENHOME_CONTRACTS_DIR")
        or _qualifier_root() / "data" / "contracts"
    )


def owners_path() -> Path:
    return _env_path("AGENT7_OWNERS_PATH") or _qualifier_root() / "data" / "owners.json"


def amo_task_state_path() -> Path:
    return (
        _env_path("AMO_TASK_STATE_PATH")
        or _qualifier_root() / "data" / "amo_task_state.json"
    )
