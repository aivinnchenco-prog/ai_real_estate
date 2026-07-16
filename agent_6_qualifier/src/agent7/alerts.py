"""Оповещения об ошибках проекта в отдельный информационный TG-бот.

Это обычный бот (Bot API), НЕ userbot: ему нужен только токен от @BotFather
и chat_id владельца (ERROR_BOT_TOKEN / ERROR_CHAT_ID в .env).

Правила:
- Ошибка всегда пишется в консоль, даже если бот не настроен.
- Одинаковая ошибка одного компонента шлётся не чаще раза в 5 минут (антиспам).
- Сбой самой отправки никогда не роняет агента.
"""
from __future__ import annotations

import os
import re
import time

import requests

_DEDUP_WINDOW_SEC = 300
_last_sent: dict[tuple[str, str], float] = {}

# Секреты в текстах ошибок (например, ?key=... в URL Gemini) наружу не отдаём.
_SECRET_RE = re.compile(r"(key|token|api_key|apikey|access_token)=[^\s&\"']+", re.IGNORECASE)


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1=***", text or "")


def notify_error(component: str, error: str, context: str = "") -> bool:
    """component — откуда («gemini», «amo», «notion»...), error — суть, context — детали."""
    error = _redact(error)
    context = _redact(context)
    print(f"[ОШИБКА][{component}] {error}" + (f" | {context}" if context else ""))

    token = os.environ.get("ERROR_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("ERROR_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    key = (component, error[:80])
    now = time.time()
    if now - _last_sent.get(key, 0.0) < _DEDUP_WINDOW_SEC:
        return False
    _last_sent[key] = now

    text = f"[Agent 7/8] ОШИБКА: {component}\n\n{error}"
    if context:
        text += f"\n\nКонтекст: {context}"
    return _send(token, chat_id, text)


def notify_manager(text: str, *, dedup_key: str = "") -> bool:
    """Вызов живого менеджера (не ошибка): клиент ждёт ответа человека.

    Идёт в бот менеджера (MANAGER_BOT_TOKEN / MANAGER_CHAT_ID в .env);
    если он не настроен — в общий информационный бот ошибок.
    Антиспам: одинаковый вызов (dedup_key) не чаще раза в 5 минут.
    """
    print(f"[МЕНЕДЖЕР] {text}")
    token = (os.environ.get("MANAGER_BOT_TOKEN", "").strip()
             or os.environ.get("ERROR_BOT_TOKEN", "").strip())
    chat_id = (os.environ.get("MANAGER_CHAT_ID", "").strip()
               or os.environ.get("ERROR_CHAT_ID", "").strip())
    if not token or not chat_id:
        return False

    key = ("manager", dedup_key or text[:80])
    now = time.time()
    if now - _last_sent.get(key, 0.0) < _DEDUP_WINDOW_SEC:
        return False
    _last_sent[key] = now
    return _send(token, chat_id, f"🔔 НУЖЕН МЕНЕДЖЕР\n\n{text}")


def _send(token: str, chat_id: str, text: str) -> bool:
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000]},
            timeout=10,
        )
        return True
    except Exception as e:
        print(f"[alerts] не удалось отправить в TG: {e}")
        return False
