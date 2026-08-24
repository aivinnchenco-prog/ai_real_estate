from __future__ import annotations

import os
import sys
from pathlib import Path

from ..dotenv_util import package_root


def _repo_root() -> Path:
    return package_root().parent


def _ensure_shared_imports() -> None:
    root = _repo_root()
    for path in (root, root / "agent_6_qualifier" / "src"):
        if path.exists():
            entry = str(path)
            if entry not in sys.path:
                sys.path.insert(0, entry)


DEFAULT_AGENT7_PROFILE_NAME = "facebook_agent7"


def facebook_profile_dir() -> Path:
    _ensure_shared_imports()
    try:
        from agent7.profile_lock import facebook_profile_dir as agent7_dir

        path = agent7_dir()
    except ImportError:
        fb_cfg_path = package_root() / "config" / "fb_browser.json"
        default = "/opt/openhome/runtime/browser_profiles/facebook_agent7"
        env_name = "AGENT7_FACEBOOK_PROFILE_DIR"
        if fb_cfg_path.exists():
            import json

            with fb_cfg_path.open(encoding="utf-8") as f:
                profile = (json.load(f).get("profile") or {})
            default = profile.get("default_profile_dir") or default
            env_name = profile.get("profile_dir_env") or env_name
        raw = os.getenv(env_name, "").strip() or default
        path = Path(raw).expanduser().resolve()
    _assert_agent7_profile(path)
    return path


def _assert_agent7_profile(path: Path) -> None:
    """Публикация только через изолированный профиль Agent 6/7, не Agent 1 parser."""
    if path.name != DEFAULT_AGENT7_PROFILE_NAME:
        raise RuntimeError(
            f"Неверный Facebook-профиль для публикации: {path}. "
            f"Допустим только каталог «{DEFAULT_AGENT7_PROFILE_NAME}» "
            "(Agent 6/7). Не используйте FB_BROWSER_PROFILE, .fb_profile Agent 4 "
            "или agent_1_parser — это другие аккаунты."
        )
    forbidden = (
        "agent_1_parser",
        ".fb_profile",
        "facebook_profile",
        "facebook_agent9",
        "facebook_owner_outreach",
    )
    low = str(path).lower()
    for token in forbidden:
        if token in low and DEFAULT_AGENT7_PROFILE_NAME not in low:
            raise RuntimeError(
                f"Профиль {path} похож на чужой FB-аккаунт ({token}). "
                f"Нужен только {DEFAULT_AGENT7_PROFILE_NAME}."
            )


def facebook_profile_lock(profile_dir: Path | None = None):
    _ensure_shared_imports()
    from openhome_shared.facebook_profile_lock import facebook_profile_lock as lock

    return lock(profile_dir or facebook_profile_dir())


def resolve_headless() -> bool:
    fb_cfg: dict = {}
    path = package_root() / "config" / "fb_browser.json"
    if path.exists():
        import json

        with path.open(encoding="utf-8") as f:
            fb_cfg = json.load(f)
    profile = fb_cfg.get("profile") or {}
    env_name = profile.get("headless_env") or "AGENT7_FB_HEADLESS"
    raw = os.getenv(env_name, "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return bool(profile.get("default_headless", False))
