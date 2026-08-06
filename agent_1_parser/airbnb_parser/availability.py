"""Календарь доступности Airbnb для ценового модуля (monthly_pricing).

Тот же приём, что в Qualifier (agent_6): Playwright открывает страницу
листинга и перехватывает штатный ответ PdpAvailabilityCalendar — Airbnb
сам присылает ~12 месяцев вперёд с флагом available по каждому дню.
Никакого перебора запросами по одному дню.

Если Playwright не установлен или Airbnb не отдал календарь — возвращаем
пустой словарь: monthly_pricing тогда работает только по уровню 1
(запрос полного месяца).
"""

from __future__ import annotations

from datetime import date

from CustomLogger import logger

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def fetch_calendar_days(listing_url: str, timeout_s: int = 45) -> dict[date, bool]:
    """{день: available} на ~12 месяцев вперёд. Пустой dict — данных нет."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright не установлен — календарь доступности пропущен "
                       "(pip install playwright && playwright install chromium)")
        return {}

    captured: list[dict] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(locale="ru-RU", user_agent=_UA)
            page = ctx.new_page()

            def on_response(resp):
                if "PdpAvailabilityCalendar" in resp.url:
                    try:
                        captured.append(resp.json())
                    except Exception:
                        pass

            page.on("response", on_response)
            page.goto(listing_url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            for _ in range(timeout_s):
                if captured:
                    break
                page.wait_for_timeout(1000)
            browser.close()
    except Exception as e:
        logger.warning(f"Календарь Airbnb недоступен: {e}")
        return {}

    if not captured:
        logger.warning("Airbnb не отдал календарь (капча/блокировка?)")
        return {}

    days: dict[date, bool] = {}
    try:
        months = captured[0]["data"]["merlin"]["pdpAvailabilityCalendar"]["calendarMonths"]
        for m in months:
            for d in m["days"]:
                days[date.fromisoformat(d["calendarDate"])] = bool(d["available"])
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning(f"Не удалось разобрать календарь Airbnb: {e}")
        return {}

    logger.info(f"Календарь Airbnb: {len(days)} дней, "
                f"занято {sum(1 for ok in days.values() if not ok)}")
    return days
