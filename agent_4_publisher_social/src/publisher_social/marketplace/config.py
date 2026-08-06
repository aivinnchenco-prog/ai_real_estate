from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..dotenv_util import package_root

_DEFAULT_UI: dict[str, Any] = {
    "mode": "deterministic",
    "confidence": {
        "execute": 0.9,
        "recheck": 0.7,
        "vision_execute": 0.92,
        "coordinate_execute": 0.97,
    },
    "timeouts": {
        "ui_change_seconds": 10,
        "element_wait_seconds": 8,
        "media_upload_seconds": 60,
    },
    "retries": {
        "find_element": 3,
        "input_verification": 2,
        "state_transition": 2,
    },
    "vision": {
        "enabled": False,
        "coordinate_fallback_enabled": False,
        "provider": "disabled",
    },
    "diagnostics": {
        "enabled": True,
        "save_screenshot": True,
        "save_ui_dump": True,
        "retention_days": 14,
        "root_dir": "diagnostics",
    },
    "field_bands": {
        "description_y_min": 620,
        "description_y_max": 950,
        "tags_y_min": 960,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_marketplace_ui_config(publisher_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load marketplace UI config with safe defaults (backward compatible)."""
    cfg = deepcopy(_DEFAULT_UI)
    path = package_root() / "config" / "marketplace_ui.json"
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            file_cfg = json.load(handle)
        if isinstance(file_cfg, dict):
            cfg = _deep_merge(cfg, file_cfg)
    if publisher_cfg:
        pub_mp = publisher_cfg.get("marketplace_ui")
        if isinstance(pub_mp, dict):
            cfg = _deep_merge(cfg, pub_mp)
    return cfg


def load_marketplace_selectors() -> dict[str, Any]:
    path = package_root() / "config" / "marketplace_selectors.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return raw if isinstance(raw, dict) else {}


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret|cookie|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._-]+"),
)


def redact_secrets(text: str) -> str:
    lines: list[str] = []
    for line in (text or "").splitlines():
        masked = line
        for pattern in _SECRET_PATTERNS:
            if pattern.search(masked):
                masked = pattern.sub(r"\1 [REDACTED]", masked)
        lines.append(masked)
    return "\n".join(lines)
