"""Проверка доступности дат по ссылке из колонки «Календарь» в Notion.

Поддерживаемые типы ссылок (определяются автоматически):
- Google Sheets управляющей компании — сетки месяцев, занятые дни залиты
  красным (пример: calendar 2025-2026, вкладка на каждый объект);
- iCal (.ics) — стандартный экспорт броней (Airbnb ical, Google Calendar ical);
- Google Calendar (embed-ссылка) — конвертируется в публичный iCal;
- Airbnb-объявление — Playwright-проверка (пока заглушка, см. airbnb_check.py).

Семантика дат: проверяются НОЧИ проживания, т.е. дни [check_in, check_out).
День выезда может совпадать с чьим-то заездом — это не конфликт.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import requests

from .airbnb_check import CalendarCheck, check_airbnb_dates

_MONTHS = {
    "january": 1, "february": 2, "fabruary": 2,  # опечатка в живой таблице УК
    "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
# Заливки, означающие «день свободен» (нет заливки / белый фон)
_FREE_FILLS = {None, "00000000", "FFFFFFFF"}


def _nights(check_in: date, check_out: date) -> list[date]:
    n = max((check_out - check_in).days, 1)
    return [check_in + timedelta(days=i) for i in range(n)]


def _ranges(days: list[date]) -> list[tuple[date, date]]:
    """Сжимает отсортированные даты в непрерывные диапазоны."""
    out: list[tuple[date, date]] = []
    for d in sorted(days):
        if out and (d - out[-1][1]).days == 1:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


# ---------- Google Sheets управляющей компании ----------

def _month_number(text: str) -> int | None:
    """Точное имя месяца («March», «october»); «March 2026» тоже допустимо."""
    first = str(text).strip().lower().split()
    return _MONTHS.get(first[0]) if first else None


def _blocked_days_from_sheet(ws) -> tuple[set[date], set[date], set[tuple[int, int]]]:
    """Читает лист xlsx: (занято — красный, «под вопросом» — прочие заливки, покрытые месяцы).

    Структура листа: ячейки-года (2025/2026) или год в заголовке месяца
    («March 2026»), ниже строка дней недели и сетка чисел;
    занятый день залит красным, свободный — без заливки.
    """
    years: list[tuple[int, int, int]] = []        # (row, col, year)
    months: list[tuple[int, int, int, int]] = []  # (row, col, month, year|0)
    for row in ws.iter_rows():
        for c in row:
            v = c.value
            if isinstance(v, (int, float)) and 2000 <= v <= 2100 and float(v).is_integer():
                years.append((c.row, c.column, int(v)))
            elif isinstance(v, str):
                m = _month_number(v)
                if m:
                    tokens = v.strip().split()
                    inline_year = (int(tokens[1])
                                   if len(tokens) > 1 and tokens[1].isdigit()
                                   and 2000 <= int(tokens[1]) <= 2100 else 0)
                    months.append((c.row, c.column, m, inline_year))

    busy: set[date] = set()
    tentative: set[date] = set()
    covered: set[tuple[int, int]] = set()
    for mrow, mcol, month, inline_year in months:
        year = inline_year
        if not year:
            # Год: ближайшая слева/выше ячейка-год над заголовком месяца.
            candidates = [(yc, yr, yy) for yr, yc, yy in years
                          if yr < mrow and yc <= mcol]
            if not candidates:
                continue
            year = max(candidates)[2]
        covered.add((year, month))
        # Сетка: до 7 строк чисел, 7 колонок от заголовка месяца.
        for r in range(mrow + 2, mrow + 9):
            for col in range(mcol, mcol + 7):
                cell = ws.cell(row=r, column=col)
                v = cell.value
                if not (isinstance(v, (int, float)) and 1 <= v <= 31 and float(v).is_integer()):
                    continue
                try:
                    day = date(year, month, int(v))
                except ValueError:
                    continue
                fill = cell.fill.start_color.rgb if (cell.fill and cell.fill.start_color) else None
                if fill in _FREE_FILLS:
                    continue
                if fill == "FFFF0000":
                    busy.add(day)
                else:
                    tentative.add(day)  # жёлтый и прочие цвета — «под вопросом»
    return busy, tentative, covered


def check_gsheet_dates(url: str, check_in: date, check_out: date) -> CalendarCheck:
    """Календарь УК в Google Sheets: красный день = занято.

    Ссылка должна вести на вкладку объекта (…#gid=…). Экспортируем книгу
    в xlsx (заливки сохраняются), лист находим по gid через CSV-экспорт A1.
    """
    import openpyxl

    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    if not m:
        raise ValueError(f"не похоже на ссылку Google Sheets: {url}")
    doc_id = m.group(1)
    base = f"https://docs.google.com/spreadsheets/d/{doc_id}"

    gid = None
    frag_qs = parse_qs(urlparse(url).fragment) | parse_qs(urlparse(url).query)
    if frag_qs.get("gid"):
        gid = frag_qs["gid"][0]

    r = requests.get(f"{base}/export?format=xlsx", timeout=60)
    r.raise_for_status()
    wb = openpyxl.load_workbook(io.BytesIO(r.content))

    ws = wb[wb.sheetnames[0]]
    if gid:
        # Имя вкладки по gid — из htmlview (доступен для публичных таблиц).
        rh = requests.get(f"{base}/htmlview", timeout=60)
        if rh.ok:
            pairs = re.findall(r'items\.push\(\{name: "([^"]+)",[^}]*?gid: "(\d+)"', rh.text)
            name = next((n for n, g in pairs if g == gid), None)
            if name and name in wb.sheetnames:
                ws = wb[name]

    busy, tentative, covered = _blocked_days_from_sheet(ws)
    if not covered:
        # Ни одного месяца не распознано — формат не тот: не рискуем.
        return CalendarCheck(available=None, blocked_ranges=[],
                             note="формат календаря не распознан")

    nights = _nights(check_in, check_out)
    if any((n.year, n.month) not in covered for n in nights):
        return CalendarCheck(available=None,
                             blocked_ranges=_ranges(sorted(busy)),
                             note="даты клиента вне горизонта календаря")

    today = date.today()
    future_busy = _ranges([d for d in busy if d >= today])

    hit_busy = [n for n in nights if n in busy]
    hit_tent = [n for n in nights if n in tentative]
    if hit_busy:
        return CalendarCheck(available=False, blocked_ranges=_ranges(hit_busy),
                             note="красные дни в календаре УК",
                             future_busy=future_busy)
    if hit_tent:
        return CalendarCheck(available=None, blocked_ranges=_ranges(hit_tent),
                             note="дни «под вопросом» (жёлтые) — уточнить у владельца",
                             future_busy=future_busy)
    return CalendarCheck(available=True, blocked_ranges=[], future_busy=future_busy)


# ---------- iCal (.ics): Airbnb ical, Google Calendar ical и т.п. ----------

def _parse_ics_dates(text: str) -> list[tuple[date, date]]:
    """Пары (DTSTART, DTEND) всех VEVENT. DTEND в iCal — эксклюзивный."""
    events: list[tuple[date, date]] = []
    start = end = None
    for line in text.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            start = end = None
        elif line.startswith("DTSTART"):
            start = _ics_date(line)
        elif line.startswith("DTEND"):
            end = _ics_date(line)
        elif line == "END:VEVENT" and start:
            events.append((start, end or start + timedelta(days=1)))
    return events


def _ics_date(line: str) -> date | None:
    m = re.search(r":(\d{8})(T\d{6}Z?)?", line)
    if not m:
        return None
    d = datetime.strptime(m.group(1), "%Y%m%d").date()
    return d


def check_ical_dates(url: str, check_in: date, check_out: date) -> CalendarCheck:
    """Занятость по iCal-фиду: событие = бронь (стандарт Airbnb/Booking/GCal)."""
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    events = _parse_ics_dates(r.text)

    today = date.today()
    future_busy = _ranges(sorted({
        ev_start + timedelta(days=i)
        for ev_start, ev_end in events
        for i in range((ev_end - ev_start).days)
        if ev_start + timedelta(days=i) >= today
    }))

    nights = set(_nights(check_in, check_out))
    blocked: list[date] = []
    for ev_start, ev_end in events:
        for n in sorted(nights):
            if ev_start <= n < ev_end:
                blocked.append(n)
    if blocked:
        return CalendarCheck(available=False, blocked_ranges=_ranges(blocked),
                             note="пересечение с бронью в iCal",
                             future_busy=future_busy)
    return CalendarCheck(available=True, blocked_ranges=[], future_busy=future_busy)


def _gcal_embed_to_ical(url: str) -> str | None:
    """Публичная embed-ссылка Google Calendar -> ссылка на её iCal-фид."""
    qs = parse_qs(urlparse(url).query)
    src = (qs.get("src") or [None])[0]
    if src:
        return f"https://calendar.google.com/calendar/ical/{src}/public/basic.ics"
    return None


# ---------- результат проверки -> запись в Notion ----------

def format_busy_ranges(ranges: list[tuple[date, date]], limit: int = 6) -> str:
    """«14.07–18.07.2026, 03.08–10.08.2026» для колонки «Будущие брони»."""
    parts = []
    for start, end in ranges[:limit]:
        if start == end:
            parts.append(start.strftime("%d.%m.%Y"))
        else:
            parts.append(f"{start.strftime('%d.%m')}–{end.strftime('%d.%m.%Y')}")
    if len(ranges) > limit:
        parts.append("…")
    return ", ".join(parts)


def notion_update_from_precheck(check: CalendarCheck, check_in: date) -> dict | None:
    """Аргументы notion_store.update_availability по результату проверки календаря.

    None — проверка не дала вердикта (не пишем в Notion, чтобы не затирать).
    """
    from agent7.models import Availability

    if check.available is True:
        return {
            "status": Availability.FREE,
            "busy_until": None,
            "future_bookings": format_busy_ranges(check.future_busy),
        }
    if check.available is False:
        return {
            "status": Availability.BUSY,
            "busy_until": check.busy_until(check_in),
            "future_bookings": format_busy_ranges(check.future_busy),
        }
    return None


# ---------- диспетчер ----------

def check_calendar_dates(url: str, check_in: date, check_out: date) -> CalendarCheck:
    """Выбирает способ проверки по виду ссылки из колонки «Календарь»."""
    u = url.strip()
    low = u.lower()
    if "docs.google.com/spreadsheets" in low:
        return check_gsheet_dates(u, check_in, check_out)
    if low.endswith(".ics") or "/ical/" in low or "format=ical" in low:
        return check_ical_dates(u, check_in, check_out)
    if "calendar.google.com" in low:
        ical = _gcal_embed_to_ical(u)
        if ical:
            return check_ical_dates(ical, check_in, check_out)
        return check_ical_dates(u, check_in, check_out)
    if "airbnb." in low:
        return check_airbnb_dates(u, check_in, check_out)  # Playwright, этап 4
    raise NotImplementedError(f"неизвестный тип календаря: {u}")
