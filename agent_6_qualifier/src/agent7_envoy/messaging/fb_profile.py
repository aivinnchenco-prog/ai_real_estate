"""Resolve Facebook browser profile for Agent7 owner outreach."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_AGENT7_FB_PROFILE_DIR = "/opt/openhome/runtime/browser_profiles/facebook_agent7"


def repo_root() -> Path:
    # agent7_envoy/messaging/fb_profile.py → parents[4] = refactor root
    return Path(__file__).resolve().parents[4]


def fb_parser_root() -> Path:
    return repo_root() / "agent_1_parser" / "fb_parser"


def default_parser_fb_profile_dir() -> Path:
    """Legacy parser profile (Agent 1). Not used for Agent 7 unless explicitly overridden."""
    raw = (os.getenv("FB_BROWSER_PROFILE") or ".fb_profile").strip() or ".fb_profile"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = fb_parser_root() / path
    return path.resolve()


def resolve_agent7_facebook_profile_dir() -> Path:
    """Profile for Agent7 Facebook Messenger.

    Priority:
    1. AGENT7_FACEBOOK_PROFILE_DIR (explicit override)
    2. DEFAULT_AGENT7_FB_PROFILE_DIR (isolated owner-outreach profile)
    """
    override = (os.getenv("AGENT7_FACEBOOK_PROFILE_DIR") or "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            path = repo_root() / path
        return path.resolve()
    return Path(DEFAULT_AGENT7_FB_PROFILE_DIR).expanduser().resolve()


def facebook_profile_has_session(profile_path: Path) -> bool:
    """True when Chromium profile looks logged into Facebook (c_user cookie)."""
    cookies_db = profile_path / "Default" / "Cookies"
    if not cookies_db.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM cookies WHERE host_key LIKE '%facebook.com%' "
            "AND name='c_user' LIMIT 1"
        )
        ok = cur.fetchone() is not None
        conn.close()
        return ok
    except Exception:
        try:
            return cookies_db.stat().st_size > 10_000
        except OSError:
            return False
