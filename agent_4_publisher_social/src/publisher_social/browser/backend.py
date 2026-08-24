from __future__ import annotations

import os
from typing import Any

from ..config import load_publisher_config
from ..dotenv_util import package_root


def load_fb_browser_config() -> dict[str, Any]:
    path = package_root() / "config" / "fb_browser.json"
    if not path.exists():
        return {}
    import json

    with path.open(encoding="utf-8") as f:
        return json.load(f)


def fb_backend(publisher_cfg: dict[str, Any] | None = None) -> str:
    """phone (Termux/ADB) or browser (Playwright + Agent 7 profile)."""
    cfg = publisher_cfg or load_publisher_config()
    env_name = (cfg.get("fb_publisher") or {}).get("backend_env") or "PUBLISHER_FB_BACKEND"
    env = os.getenv(env_name, "").strip().lower()
    if env in ("browser", "phone"):
        return env
    fb_cfg = load_fb_browser_config()
    configured = (cfg.get("fb_publisher") or {}).get("backend") or fb_cfg.get("backend") or "phone"
    return str(configured).strip().lower() or "phone"


def channel_uses_browser(channel: str, publisher_cfg: dict[str, Any] | None = None) -> bool:
    if channel not in ("fb_groups", "fb_marketplace"):
        return False
    return fb_backend(publisher_cfg) == "browser"
