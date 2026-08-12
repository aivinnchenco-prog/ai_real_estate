#!/usr/bin/env python3
"""Read-only Telegram MTProto diagnostic (no messages sent).

  python3 scripts/tg_diagnose.py

Uses TG_API_ID / TG_API_HASH / TG_SESSION from .env (or env).
Optional identity check: TG_EXPECTED_USER_ID, TG_EXPECTED_USERNAME.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent6_qualifier.tg_account import (  # noqa: E402
    identity_check,
    mask_phone,
    session_file_path,
    session_name,
)
from agent6_qualifier.tg_userbot import load_env, make_client  # noqa: E402


async def _run() -> int:
    load_env()
    missing = [k for k in ("TG_API_ID", "TG_API_HASH") if not os.environ.get(k, "").strip()]
    if missing:
        print(f"Config: missing {', '.join(missing)}")
        return 1

    print("Mode: MTProto user account")
    print(f"Session name: {session_name()}")
    print(f"Session file: {session_file_path()}")
    print(f"Session file exists: {'YES' if session_file_path().exists() else 'NO'}")

    client = make_client()
    try:
        await client.connect()
        authorized = await client.is_user_authorized()
        print(f"Authorized: {'YES' if authorized else 'NO'}")
        if not authorized:
            print("Connection: connected (not authorized — run scripts/tg_login.py)")
            return 0

        me = await client.get_me()
        print(f"User ID: {me.id}")
        if me.username:
            print(f"Username: @{me.username}")
        else:
            print("Username: (none)")
        print(f"Phone: {mask_phone(me.phone)}")
        print("Connection: connected")

        check = identity_check(me)
        if check == "SKIP":
            print("Expected identity: (not configured)")
        else:
            print(f"Expected identity: {check}")
        return 0
    finally:
        await client.disconnect()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
