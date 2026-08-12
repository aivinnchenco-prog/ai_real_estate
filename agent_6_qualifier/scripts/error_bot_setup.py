"""Помощник настройки бота ошибок: показывает chat_id для ERROR_CHAT_ID.

Порядок:
1. Создайте бота у @BotFather (/newbot), токен впишите в .env -> ERROR_BOT_TOKEN.
2. Напишите новому боту любое сообщение (например /start) со своего аккаунта.
3. Запустите:  python3 scripts/error_bot_setup.py
   Скрипт покажет ваш chat_id — впишите его в .env -> ERROR_CHAT_ID.
4. Скрипт сразу отправит тестовое сообщение для проверки.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip())

token = os.environ.get("ERROR_BOT_TOKEN", "").strip()
if not token:
    print("ERROR_BOT_TOKEN пуст. Создайте бота у @BotFather и впишите токен в .env")
    sys.exit(1)

r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
updates = r.json().get("result", [])
chats = {}
for u in updates:
    msg = u.get("message") or u.get("edited_message") or {}
    chat = msg.get("chat") or {}
    if chat.get("id"):
        chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name", "")

if not chats:
    print("Сообщений боту ещё не было. Напишите боту /start и запустите скрипт снова.")
    sys.exit(1)

print("Найденные чаты (chat_id — кто):")
for cid, who in chats.items():
    print(f"  {cid} — {who}")

configured = os.environ.get("ERROR_CHAT_ID", "").strip()
target = configured or str(list(chats.keys())[0])
r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  json={"chat_id": target, "text": "[Agent 7/8] Бот ошибок подключён. Тест OK."},
                  timeout=15)
if r.ok:
    print(f"\nТестовое сообщение отправлено в chat_id={target}.")
    if not configured:
        print(f"Впишите в .env:  ERROR_CHAT_ID={target}")
else:
    print(f"Не удалось отправить тест: {r.text[:200]}")
