"""Цены Airbnb по месяцам: monthly / prorated / insufficient_data.

Чистая логика без сети. Сетевые запросы (цена за период) передаются
снаружи функцией fetch_price(check_in: date, check_out: date) -> float | None.

Алгоритм на месяц (максимум 2 запроса):
  УРОВЕНЬ 1 — полный месяц (1-е -> последнее число). Если Airbnb вернул цену — status "monthly".
  УРОВЕНЬ 2 — фолбэк: самый длинный непрерывный доступный отрезок месяца
  (доступность берём из уже спарсенного календаря, БЕЗ перебора по дням),
  запрос цены за отрезок и экстраполяция пропорцией на 30 дней.

Правила:
  - минимальный отрезок для экстраполяции — 5 дней, иначе insufficient_data;
  - если несколько отрезков — берём самый длинный, один запрос;
  - округление цены до сотен THB;
  - количество месяцев вперёд — параметр months_ahead (PRICE_MONTHS_AHEAD).
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Callable, Optional

MIN_SEGMENT_DAYS = 5
EXTRAPOLATION_BASE_DAYS = 30

FetchPrice = Callable[[date, date], Optional[float]]


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """Первое и последнее число месяца."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def iter_months_ahead(today: date, months_ahead: int) -> list[tuple[int, int]]:
    """(year, month) на months_ahead месяцев вперёд, начиная со следующего месяца."""
    out = []
    y, m = today.year, today.month
    for _ in range(months_ahead):
        m += 1
        if m > 12:
            m, y = 1, y + 1
        out.append((y, m))
    return out


def longest_available_segment(
    availability: dict[date, bool], year: int, month: int
) -> tuple[date, date] | None:
    """Самый длинный непрерывный доступный отрезок внутри месяца.

    availability: словарь день -> доступен ли (из календаря Airbnb).
    Дни, которых нет в словаре, считаются недоступными (нет данных).
    """
    first, last = month_bounds(year, month)
    best: tuple[date, date] | None = None
    seg_start: date | None = None

    day = first
    while day <= last:
        if availability.get(day):
            if seg_start is None:
                seg_start = day
            if best is None or (day - seg_start) > (best[1] - best[0]):
                best = (seg_start, day)
        else:
            seg_start = None
        day += timedelta(days=1)
    return best


def segment_days(segment: tuple[date, date]) -> int:
    """Дней в отрезке включительно (17..30 сентября = 14 дней)."""
    return (segment[1] - segment[0]).days + 1


def month_fully_available(availability: dict[date, bool], year: int, month: int) -> bool | None:
    """True — весь месяц доступен, False — есть занятые дни, None — данных нет."""
    first, last = month_bounds(year, month)
    day, seen = first, False
    while day <= last:
        if day in availability:
            seen = True
            if not availability[day]:
                return False
        else:
            return None if not seen else False
        day += timedelta(days=1)
    return True if seen else None


def round_to_hundreds(value: float) -> int:
    return int(round(value, -2))


def extrapolate_price(segment_price: float, days: int, base_days: int = EXTRAPOLATION_BASE_DAYS) -> int:
    """price_30 = (цена_за_отрезок / дней_в_отрезке) × 30, округление до сотен."""
    return round_to_hundreds(segment_price / days * base_days)


def price_for_month(
    fetch_price: FetchPrice,
    availability: dict[date, bool],
    year: int,
    month: int,
    *,
    min_segment_days: int = MIN_SEGMENT_DAYS,
) -> dict:
    """Цена за один месяц. Возвращает dict для JSON (см. формат в докстринге модуля)."""
    first, last = month_bounds(year, month)
    fully = month_fully_available(availability, year, month)

    # УРОВЕНЬ 1 — полный месяц. Пропускаем запрос, только если календарь
    # однозначно говорит, что месяц частично занят.
    if fully is not False:
        price = fetch_price(first, last)
        if price:
            return {
                "price": round_to_hundreds(price),
                "status": "monthly",
                "period_used": f"{first.isoformat()}/{last.isoformat()}",
            }

    # УРОВЕНЬ 2 — пропорциональный расчёт по самому длинному доступному отрезку.
    segment = longest_available_segment(availability, year, month)
    available = segment_days(segment) if segment else 0

    if not segment or available < min_segment_days:
        return {
            "price": None,
            "status": "insufficient_data",
            "available_days": available,
        }

    seg_price = fetch_price(segment[0], segment[1])
    if not seg_price:
        return {
            "price": None,
            "status": "insufficient_data",
            "available_days": available,
            "note": "Airbnb не вернул цену за доступный отрезок",
        }

    return {
        "price": extrapolate_price(seg_price, available),
        "status": "prorated",
        "based_on_days": available,
        "period_used": f"{segment[0].isoformat()}/{segment[1].isoformat()}",
        "note": f"цена экстраполирована с {available} доступных дней",
    }


def entry_has_price(entry: dict | None) -> bool:
    return bool(entry and entry.get("price"))


def collect_monthly_prices(
    fetch_price: FetchPrice,
    availability: dict[date, bool],
    *,
    months_ahead: int = 12,
    today: date | None = None,
    min_segment_days: int = MIN_SEGMENT_DAYS,
    existing: dict | None = None,
    only_missing: bool = False,
) -> dict[str, dict]:
    """Цены на months_ahead месяцев вперёд: {"2026-09": {...}, ...}.

    existing + only_missing=True — не трогаем месяцы, где цена уже есть
    (кэш / предыдущая фаза / refill только insufficient_data).
    """
    today = today or date.today()
    existing = existing or {}
    result: dict[str, dict] = {}
    for year, month in iter_months_ahead(today, months_ahead):
        key = f"{year:04d}-{month:02d}"
        prev = existing.get(key)
        if only_missing and entry_has_price(prev):
            result[key] = prev
            continue
        result[key] = price_for_month(
            fetch_price, availability, year, month, min_segment_days=min_segment_days
        )
    return result


# Фабрика воркера: (fetch_price, release). release() возвращает браузер в пул.
PriceWorkerFactory = Callable[[], tuple[FetchPrice, Callable[[], None]]]


def collect_monthly_prices_parallel(
    make_worker: PriceWorkerFactory,
    availability: dict[date, bool],
    *,
    months_ahead: int = 12,
    today: date | None = None,
    min_segment_days: int = MIN_SEGMENT_DAYS,
    existing: dict | None = None,
    only_missing: bool = False,
    workers: int = 3,
) -> dict[str, dict]:
    """Как collect_monthly_prices, но до `workers` месяцев одновременно.

    make_worker() вызывается в потоке на каждый месяц — верни (fetch, release),
    где fetch привязан к своему браузеру (Selenium не thread-safe).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    today = today or date.today()
    existing = existing or {}
    workers = max(1, int(workers))
    result: dict[str, dict] = {}
    jobs: list[tuple[str, int, int]] = []

    for year, month in iter_months_ahead(today, months_ahead):
        key = f"{year:04d}-{month:02d}"
        prev = existing.get(key)
        if only_missing and entry_has_price(prev):
            result[key] = prev
            continue
        jobs.append((key, year, month))

    if not jobs:
        return result

    def _one(job: tuple[str, int, int]) -> tuple[str, dict]:
        key, year, month = job
        fetch, release = make_worker()
        try:
            entry = price_for_month(
                fetch, availability, year, month, min_segment_days=min_segment_days
            )
            return key, entry
        finally:
            release()

    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futs = [pool.submit(_one, job) for job in jobs]
        for fut in as_completed(futs):
            key, entry = fut.result()
            result[key] = entry
    return result
