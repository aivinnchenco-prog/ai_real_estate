"""File lock for Agent 9 Facebook Chromium profile."""

from __future__ import annotations

from pathlib import Path

from openhome_shared.facebook_profile_lock import facebook_profile_lock, resolve_profile_lock_path

from .config_loader import facebook_profile_dir

__all__ = ["facebook_profile_lock", "lock_path", "resolve_profile_lock_path"]


def lock_path() -> Path:
    return resolve_profile_lock_path(facebook_profile_dir())
