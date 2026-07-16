"""Человеческое поведение userbot'а (личный TG-аккаунт — не бот).

Заимствовано у tg-agent-leadgen (симулятор поведения) и CRM-BOT-2.0
(пауза на FLOOD): личный аккаунт, отвечающий мгновенно, Telegram метит
как спам-бота. Поэтому:
- перед ответом показываем «печатает…» и ждём время, пропорциональное длине;
- FloodWaitError уважаем (ждём столько, сколько просит сервер).

Квалификатор отвечает КРУГЛОСУТОЧНО — клиенты пишут из разных часовых
поясов, окна активности здесь нет (в отличие от FB-постинга).

Расчёт задержек отделён от sleep — так их можно проверить юнит-тестами.
"""
from __future__ import annotations

import asyncio
import random


def typing_delay_seconds(
    text: str,
    *,
    cps: float = 12.0,
    min_s: float = 1.2,
    max_s: float = 9.0,
    jitter: float = 0.25,
) -> float:
    """Сколько «печатать» ответ: ~cps символов/сек + случайный разброс.
    Ограничено [min_s, max_s], чтобы длинные тексты не висели минутами."""
    base = len(text or "") / max(cps, 1.0)
    base = max(min_s, min(base, max_s))
    return base * (1.0 + random.uniform(-jitter, jitter))


def read_delay_seconds(min_s: float = 0.8, max_s: float = 3.0) -> float:
    """Пауза «прочитал сообщение», прежде чем начать печатать."""
    return random.uniform(min_s, max_s)


async def humanized_respond(event, text: str, *, client=None) -> None:
    """Ответить «по-человечески»: пауза на чтение → «печатает…» → отправка.
    FloodWait от Telegram уважаем. При отсутствии typing-API просто шлём."""
    from telethon.errors import FloodWaitError

    await asyncio.sleep(read_delay_seconds())
    delay = typing_delay_seconds(text)
    try:
        async with event.client.action(event.chat_id, "typing"):
            await asyncio.sleep(delay)
    except FloodWaitError as e:
        await asyncio.sleep(getattr(e, "seconds", 5) + 1)
    except Exception:
        await asyncio.sleep(delay)  # typing-индикатор не критичен

    for attempt in range(3):
        try:
            await event.respond(text)
            return
        except FloodWaitError as e:
            wait = getattr(e, "seconds", 5)
            if attempt == 2:
                raise
            await asyncio.sleep(wait + 1)
