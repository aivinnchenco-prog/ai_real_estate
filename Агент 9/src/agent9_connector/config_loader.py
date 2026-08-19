"""Load Agent 9 config from env + JSON files."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_AGENT9_FB_PROFILE_DIR = "/opt/openhome/runtime/browser_profiles/facebook_agent9"
LEGACY_FB_PROFILE_NAME = "facebook_profile"


def data_dir() -> Path:
    raw = os.getenv("AGENT9_DATA_DIR", "").strip()
    return Path(raw) if raw else ROOT / "data"


def facebook_profile_dir() -> Path:
    """Active persistent Chromium profile for Agent 9 Messenger."""
    raw = os.getenv("AGENT9_FACEBOOK_PROFILE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(DEFAULT_AGENT9_FB_PROFILE_DIR)


def legacy_facebook_profile_dir() -> Path:
    """Previous profile location — kept as backup until manual re-auth."""
    return data_dir() / LEGACY_FB_PROFILE_NAME


def config_dir() -> Path:
    raw = os.getenv("AGENT9_CONFIG_PATH", "").strip()
    if raw:
        p = Path(raw)
        return p.parent if p.suffix == ".json" else p
    return ROOT / "config"


@lru_cache(maxsize=1)
def load_connector_config() -> dict:
    path = config_dir() / "connector.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


@lru_cache(maxsize=1)
def load_intro_templates() -> dict:
    path = config_dir() / "intro_templates.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def confidence_thresholds() -> tuple[float, float]:
    cfg = load_connector_config().get("confidence") or {}
    auto_min = float(cfg.get("auto_action_min", 0.90))
    recheck_min = float(cfg.get("recheck_min", 0.70))
    return auto_min, recheck_min


def intro_language() -> str:
    return (os.getenv("AGENT9_INTRO_LANGUAGE") or load_connector_config().get("intro_language") or "ru").strip().lower()
