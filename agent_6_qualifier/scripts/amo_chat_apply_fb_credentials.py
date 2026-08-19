#!/usr/bin/env python3
"""Apply amoCRM Facebook custom chat credentials from a local JSON file.

Never prints secrets. Expected JSON (amo support response shapes):

{
  "channel_id": "...",
  "channel_secret": "...",
  "bot_id": "...",
  "account_id": "41a97e13-a7b6-482c-9312-b7f7724952f0"
}

or:
{
  "id": "...",
  "secret_key": "...",
  "bot": {"id": "..."}
}

Usage:
  AMO_CHAT_FB_CREDENTIALS_FILE=/path/to/channel.json python3 amo_chat_apply_fb_credentials.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_CREDENTIALS_FILE = (
    "/opt/openhome/runtime/secrets/amo_chat_fb_channel.json"
)


def _normalize(payload: dict) -> dict[str, str]:
    channel_id = str(
        payload.get("channel_id") or payload.get("id") or ""
    ).strip()
    secret = str(
        payload.get("channel_secret")
        or payload.get("secret_key")
        or payload.get("secret")
        or ""
    ).strip()
    bot = payload.get("bot")
    bot_id = str(payload.get("bot_id") or "").strip()
    if not bot_id and isinstance(bot, dict):
        bot_id = str(bot.get("id") or "").strip()
    account_id = str(
        payload.get("account_id")
        or payload.get("amojo_id")
        or "41a97e13-a7b6-482c-9312-b7f7724952f0"
    ).strip()
    return {
        "AMO_CHAT_FB_CHANNEL_ID": channel_id,
        "AMO_CHAT_FB_CHANNEL_SECRET": secret,
        "AMO_CHAT_FB_BOT_ID": bot_id,
        "AMO_CHAT_ACCOUNT_ID": account_id,
    }


def main() -> int:
    cred_path = Path(
        os.getenv("AMO_CHAT_FB_CREDENTIALS_FILE") or DEFAULT_CREDENTIALS_FILE
    ).expanduser()
    if not cred_path.exists():
        print(f"MISSING: credentials file {cred_path}")
        return 2
    try:
        payload = json.loads(cred_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"INVALID_JSON: {type(exc).__name__}")
        return 3
    if not isinstance(payload, dict):
        print("INVALID_JSON: root must be object")
        return 3

    updates = _normalize(payload)
    missing = [
        k
        for k in (
            "AMO_CHAT_FB_CHANNEL_ID",
            "AMO_CHAT_FB_CHANNEL_SECRET",
            "AMO_CHAT_FB_BOT_ID",
            "AMO_CHAT_ACCOUNT_ID",
        )
        if not updates.get(k)
    ]
    if missing:
        print(f"INCOMPLETE: {', '.join(missing)}")
        return 4

    # Reject channel code mistaken for channel_id (amo.ext.* is not connect id).
    cid = updates["AMO_CHAT_FB_CHANNEL_ID"]
    if cid.startswith("amo.ext."):
        print("INVALID_CHANNEL_ID: use amo-provided channel id, not amo.ext code")
        return 5

    from agent7_envoy.amo_chat.config import default_env_path, update_env_file

    env_path = default_env_path()
    update_env_file(env_path, updates)
    print(f"APPLIED: facebook channel credentials -> {env_path} (secrets not logged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
