"""Первый вход в личный TG-аккаунт: создаёт файл сессии Telethon.

Запуск:  python3 scripts/tg_login.py
Спросит номер телефона аккаунта и код подтверждения из Telegram (один раз).
После успеха появится файл {TG_SESSION}.session — его не коммитить (уже в .gitignore).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent6_qualifier.tg_userbot import load_env, make_client  # noqa: E402


async def main() -> None:
    load_env()
    client = make_client()
    await client.start()  # интерактивно спросит телефон и код
    me = await client.get_me()
    print(f"Успешно: вошли как {me.first_name} (@{me.username or 'без username'})")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
