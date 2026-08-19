"""File lock for Agent 7 Facebook Chromium profile (owner communication)."""

from __future__ import annotations

import os
from pathlib import Path

from openhome_shared.facebook_profile_lock import facebook_profile_lock, resolve_profile_lock_path

__all__ = [
    "DEFAULT_AGENT7_FB_PROFILE_DIR",
    "facebook_profile_dir",
    "facebook_profile_lock",
    "lock_path",
    "resolve_profile_lock_path",
]

DEFAULT_AGENT7_FB_PROFILE_DIR = "/opt/openhome/runtime/browser_profiles/facebook_agent7"


def facebook_profile_dir() -> Path:
    raw = os.getenv("AGENT7_FACEBOOK_PROFILE_DIR", "").strip() or DEFAULT_AGENT7_FB_PROFILE_DIR
    return Path(raw).expanduser().resolve()


def lock_path() -> Path:
    return resolve_profile_lock_path(facebook_profile_dir())
