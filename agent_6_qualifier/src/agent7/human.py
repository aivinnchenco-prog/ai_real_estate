"""Человеческое поведение userbot'а (личный TG-аккаунт — не бот).

Заимствовано у tg-agent-leadgen (симулятор поведения) и CRM-BOT-2.0
(пауза на FLOOD): личный аккаунт, отвечающий мгновенно и круглосуточно,
Telegram метит как спам-бота. Поэтому:
- перед ответом показываем «печатает…» и ждём время, пропорциональное длине;
- ночью не пишем (окно активности), задержки рандомизируем;
- FloodWaitError уважаем (ждём столько, сколько просит сервер).

Расчёт задержек отделён от sleep — так их можно проверить юнит-тестами.
"""
from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timedelta, timezone


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


def _parse_hhmm(raw: str) -> tuple[int, int]:
    h, _, m = str(raw).partition(":")
    return int(h), int(m or 0)


def within_active_hours(
    now_utc: datetime | None = None,
    *,
    tz_offset_hours: int | None = None,
    start: str | None = None,
    end: str | None = None,
) -> bool:
    """Окно активности по местному времени (по умолчанию Пхукет UTC+7,
    09:00–22:30). Настраивается TG_ACTIVE_START/END/TG_TZ_OFFSET в .env."""
    now_utc = now_utc or datetime.now(timezone.utc)
    tz = tz_offset_hours if tz_offset_hours is not None else int(os.environ.get("TG_TZ_OFFSET", "7"))
    start = start or os.environ.get("TG_ACTIVE_START", "09:00")
    end = end or os.environ.get("TG_ACTIVE_END", "22:30")
    local = now_utc + timedelta(hours=tz)
    minutes = local.hour * 60 + local.minute
    sh, sm = _parse_hhmm(start)
    eh, em = _parse_hhmm(end)
    return sh * 60 + sm <= minutes <= eh * 60 + em


def seconds_until_active(
    now_utc: datetime | None = None,
    *,
    tz_offset_hours: int | None = None,
    start: str | None = None,
) -> float:
    """Сколько ждать до открытия окна активности (0, если уже открыто)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if within_active_hours(now_utc, tz_offset_hours=tz_offset_hours, start=start):
        return 0.0
    tz = tz_offset_hours if tz_offset_hours is not None else int(os.environ.get("TG_TZ_OFFSET", "7"))
    start = start or os.environ.get("TG_ACTIVE_START", "09:00")
    sh, sm = _parse_hhmm(start)
    local = now_utc + timedelta(hours=tz)
    target = local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    if local >= target:
        target += timedelta(days=1)
    return (target - local).total_seconds()


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
