from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .dotenv_util import package_root


def load_publisher_config() -> dict[str, Any]:
    path = package_root() / "config" / "publisher.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_android_config() -> dict[str, Any]:
    pub = load_publisher_config()
    rel = pub.get("android", {}).get("config_file", "config/android.json")
    path = package_root() / rel
    with path.open(encoding="utf-8") as f:
        cfg = json.load(f)
    if os.environ.get("ANDROID_MEDIA_DIR"):
        cfg["media_dir"] = os.environ["ANDROID_MEDIA_DIR"]
    if os.environ.get("ANDROID_SERIAL"):
        cfg["serial"] = os.environ["ANDROID_SERIAL"]
    return cfg


def notion_fields(config: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = config or load_publisher_config()
    return dict(cfg["notion"]["fields"])


def status_ready(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_publisher_config()
    return (
        os.environ.get("NOTION_STATUS_READY")
        or cfg["notion"]["statuses"]["ready"]
    )


def media_cache_dir(config: dict[str, Any] | None = None) -> Path:
    cfg = config or load_publisher_config()
    rel = cfg.get("media", {}).get("cache_dir", "data/media")
    path = package_root() / rel
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    path = package_root() / "data" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_fb_groups() -> list[str]:
    cfg = load_publisher_config()
    rel = cfg.get("fb_groups", {}).get("groups_file", "config/fb_groups_list.txt")
    path = package_root() / rel
    if not path.exists():
        return []
    groups: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        groups.append(line)
    return groups


PRODUCTION_CHANNELS = ("fb_groups", "fb_marketplace")


def production_channels(config: dict[str, Any] | None = None) -> list[str]:
    """Live phone queue channels from config, restricted to production FB channels."""
    cfg = config or load_publisher_config()
    allowed = set(PRODUCTION_CHANNELS)
    raw = list(cfg.get("channels") or PRODUCTION_CHANNELS)
    return [ch for ch in raw if ch in allowed]


def validate_production_channels(channels: list[str]) -> None:
    """Reject CLI channels that are not in the phone production queue."""
    allowed = set(PRODUCTION_CHANNELS)
    unsupported = [ch for ch in channels if ch not in allowed]
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(
            f"Канал(ы) не поддерживаются телефонным publisher: {names}. "
            f"Доступны только: {', '.join(PRODUCTION_CHANNELS)}. "
            "Instagram, TikTok, YouTube и др. публикуются через PostMyPost."
        )
