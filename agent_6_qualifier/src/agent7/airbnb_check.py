"""Airbnb pre-check: реальная проверка открытых дат в календаре объявления.

Вызывается Agent 8 ПЕРЕД первым сообщением владельцу. URL берётся из колонки
«Календарь» в Notion. Если даты клиента закрыты — владельцу не пишем,
клиенту сразу сообщаем занятость; результат пишем в колонки availability.

Как работает: Playwright (headless Chromium) открывает страницу объявления
и перехватывает внутренний запрос PdpAvailabilityCalendar — Airbnb сам
присылает календарь на 12 месяцев вперёд (available по каждому дню).
Никакого скрейпинга вёрстки — только штатный ответ их API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class CalendarCheck:
    available: bool | None        # None = не удалось проверить
    blocked_ranges: list[tuple[date, date]]   # занятые НОЧИ внутри дат клиента
    note: str = ""
    future_busy: list[tuple[date, date]] = field(default_factory=list)  # все брони впереди

    def free_nights_from(self, check_in: date) -> int:
        """Сколько ночей подряд свободно, начиная с даты заезда клиента,
        до первой закрытой ночи. 0 = сама дата заезда уже занята."""
        if not self.blocked_ranges:
            return 0
        first_blocked = min(start for start, _ in self.blocked_ranges)
        return max((first_blocked - check_in).days, 0)

    def busy_until(self, check_in: date) -> date | None:
        """До какого числа занято: конец непрерывного занятого периода,
        который мешает поездке клиента (по первой закрытой ночи)."""
        first_blocked = self.blocked_ranges[0][0] if self.blocked_ranges else check_in
        for start, end in self.future_busy:
            if start <= first_blocked <= end:
                return end
        if self.blocked_ranges:
            return self.blocked_ranges[-1][1]
        return None


def _ranges(days: list[date]) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    for d in sorted(days):
        if out and (d - out[-1][1]).days == 1:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def _fetch_calendar_days(listing_url: str, timeout_s: int = 45) -> dict[date, bool]:
    """{день: available} на ~12 месяцев вперёд из PdpAvailabilityCalendar."""
    from playwright.sync_api import sync_playwright

    captured: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="ru-RU",
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()

        def on_response(resp):
            if "PdpAvailabilityCalendar" in resp.url:
                try:
                    captured.append(resp.json())
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(listing_url, wait_until="domcontentloaded",
                  timeout=timeout_s * 1000)
        for _ in range(timeout_s):
            if captured:
                break
            page.wait_for_timeout(1000)
        browser.close()

    if not captured:
        raise RuntimeError("Airbnb не отдал календарь (капча/блокировка?)")

    days: dict[date, bool] = {}
    months = captured[0]["data"]["merlin"]["pdpAvailabilityCalendar"]["calendarMonths"]
    for m in months:
        for d in m["days"]:
            days[date.fromisoformat(d["calendarDate"])] = bool(d["available"])
    return days


def check_airbnb_dates(calendar_url: str, check_in: date, check_out: date) -> CalendarCheck:
    """Открыты ли НОЧИ клиента [check_in, check_out) в календаре Airbnb."""
    try:
        days = _fetch_calendar_days(calendar_url)
    except Exception as e:
        return CalendarCheck(available=None, blocked_ranges=[],
                             note=f"Airbnb-календарь недоступен: {e}")

    today = date.today()
    future_busy = _ranges([d for d, ok in days.items() if not ok and d >= today])

    n = max((check_out - check_in).days, 1)
    nights = [check_in + timedelta(days=i) for i in range(n)]
    unknown = [x for x in nights if x not in days]
    if unknown:
        return CalendarCheck(available=None, blocked_ranges=[],
                             note="даты клиента вне горизонта календаря Airbnb",
                             future_busy=future_busy)

    blocked = [x for x in nights if not days[x]]
    if blocked:
        return CalendarCheck(available=False, blocked_ranges=_ranges(blocked),
                             note="ночи закрыты в календаре Airbnb",
                             future_busy=future_busy)
    return CalendarCheck(available=True, blocked_ranges=[], future_busy=future_busy)
