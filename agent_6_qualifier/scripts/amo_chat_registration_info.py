#!/usr/bin/env python3
"""Print safe amoCRM custom chat channel registration fields (no secrets)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[0]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


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


def main() -> int:
    _load_dotenv(REPO / ".env")
    _load_dotenv(ROOT / ".env")
    _load_dotenv(Path(os.getenv("OPENHOME_ENV_FILE") or "/opt/openhome/.env"))

    from amo_chat_account_info import fetch_account_info
    from agent7_envoy.amo_chat.registration import (
        AIRBNB_CHANNEL_CODE,
        AIRBNB_DISPLAY_NAME,
        FACEBOOK_CHANNEL_CODE,
        FACEBOOK_DISPLAY_NAME,
        build_registration_webhook_url,
        public_base_url_from_env,
        resolve_client_uuid,
    )

    try:
        info = fetch_account_info()
    except Exception as exc:
        print("ACCOUNT_ID:")
        print("UNAVAILABLE")
        print()
        print("AMOJO_ID:")
        print("UNAVAILABLE")
        print()
        print(f"ERROR: {type(exc).__name__}")
        return 1

    uuid_check = resolve_client_uuid()
    built = build_registration_webhook_url(public_base_url_from_env())
    webhook_line = built.url if built.ok else "NOT CONFIGURED"

    print("ACCOUNT_ID:")
    print(info.account_id)
    print()
    print("AMOJO_ID:")
    print(info.amojo_id)
    print()
    print("CLIENT_UUID:")
    print(uuid_check.current)
    print()
    print("FACEBOOK CHANNEL CODE:")
    print(FACEBOOK_CHANNEL_CODE)
    print()
    print("FACEBOOK DISPLAY NAME:")
    print(FACEBOOK_DISPLAY_NAME)
    print()
    print("AIRBNB CHANNEL CODE:")
    print(AIRBNB_CHANNEL_CODE)
    print()
    print("AIRBNB DISPLAY NAME:")
    print(AIRBNB_DISPLAY_NAME)
    print()
    print("WEBHOOK URL:")
    print(webhook_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
