#!/usr/bin/env python3
"""Social / messaging auth status (no secrets, no live sends)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[0]
sys.path.insert(0, str(ROOT / "src"))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _present(*names: str) -> bool:
    return any(bool(str(os.getenv(n) or "").strip()) for n in names)


def facebook_status() -> str:
    from agent7_envoy.messaging.fb_profile import (
        facebook_profile_has_session,
        resolve_agent7_facebook_profile_dir,
    )

    path = resolve_agent7_facebook_profile_dir()
    if not path.exists():
        return "USER LOGIN REQUIRED"
    if facebook_profile_has_session(path):
        return "READY"
    return "EXPIRED"


def airbnb_status() -> str:
    override = (os.getenv("AGENT7_AIRBNB_PROFILE_DIR") or "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        path = Path("/opt/openhome/runtime/browser_profiles/airbnb_owner_outreach")
    if not path.exists():
        return "USER LOGIN REQUIRED"
    try:
        if not any(path.iterdir()):
            return "USER LOGIN REQUIRED"
    except OSError:
        return "USER LOGIN REQUIRED"
    cookies = path / "Default" / "Cookies"
    if cookies.exists() and cookies.stat().st_size > 1000:
        return "READY"
    return "EXPIRED"


def telegram_status() -> str:
    sessions_dir = Path("/opt/openhome/runtime/sessions")
    local = list((ROOT).glob("*.session"))
    runtime = list(sessions_dir.glob("*.session")) if sessions_dir.exists() else []
    has_session = bool(local or runtime)
    has_api = _present("TG_API_ID", "TG_API_HASH")
    has_bot = _present(
        "TG_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "TG_BOT_TOKEN_AGENT1",
        "TG_BOT_TOKEN_FB_PARSER",
    )
    if has_session and has_api:
        return "READY"
    if has_bot and has_api:
        return "READY"
    return "CONFIG REQUIRED"


def wazzup_status() -> str:
    if _present("WAZZUP_API_KEY") and _present("WAZZUP_CHANNEL_ID"):
        return "READY"
    return "CONFIG REQUIRED"


def main() -> int:
    _load_dotenv(REPO / ".env")
    _load_dotenv(ROOT / ".env")
    _load_dotenv(Path(os.getenv("OPENHOME_ENV_FILE") or "/opt/openhome/.env"))

    print("FACEBOOK:")
    print(facebook_status())
    print()
    print("AIRBNB:")
    print(airbnb_status())
    print()
    print("TELEGRAM:")
    print(telegram_status())
    print()
    print("WHATSAPP/WAZZUP:")
    print(wazzup_status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
