"""Первый вход в личный TG-аккаунт: создаёт файл сессии Telethon.

Запуск (test session, default):
  TG_SESSION=agent7_userbot python3 scripts/tg_login.py

Официальный корпоративный аккаунт (отдельный файл сессии, test не трогаем):
  TG_SESSION=official_company python3 scripts/tg_login.py

Спросит номер телефона (или TG_PHONE из .env) и код подтверждения (один раз).
После успеха появится файл {TG_SESSION}.session — не коммитить (.gitignore).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent6_qualifier.tg_account import mask_phone, session_file_path, session_name  # noqa: E402
from agent6_qualifier.tg_userbot import load_env, make_client  # noqa: E402


async def main() -> None:
    load_env()
    phone = os.environ.get("TG_PHONE", "").strip() or None
    client = make_client()
    if phone:
        await client.start(phone=phone)
    else:
        await client.start()
    me = await client.get_me()
    await client.disconnect()

    print("Authorized: YES")
    print(f"User ID: {me.id}")
    if me.username:
        print(f"Username: @{me.username}")
    else:
        print("Username: (none)")
    print(f"Phone: {mask_phone(me.phone)}")
    print(f"Session name: {session_name()}")
    print(f"Session file: {session_file_path()}")


if __name__ == "__main__":
    asyncio.run(main())
