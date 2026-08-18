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


def load_fb_groups(config: dict[str, Any] | None = None) -> list[str]:
    cfg = config or load_publisher_config()
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
    max_groups = int(cfg.get("fb_groups", {}).get("max_groups_per_object") or 0)
    if max_groups > 0:
        groups = groups[:max_groups]
    return groups
