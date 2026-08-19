"""Russian plural agreement for numbers in client-facing templates.

Small local helper instead of a morphology dependency: the qualifier only
needs the three-form plural rule for a fixed set of nouns.
"""
from __future__ import annotations


def plural_form(n: float | int, one: str, few: str, many: str) -> str:
    """Pick the noun form agreeing with ``n`` (1 спальня / 2 спальни / 5 спален)."""
    try:
        value = abs(int(n))
    except (TypeError, ValueError):
        return many
    # Fractions take the genitive singular: 1,5 месяца — never «1,5 месяц».
    if isinstance(n, float) and not float(n).is_integer():
        return few
    if value % 10 == 1 and value % 100 != 11:
        return one
    if value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
        return few
    return many


def bedrooms_word(n: float | int) -> str:
    return plural_form(n, "спальня", "спальни", "спален")


def guests_word(n: float | int) -> str:
    return plural_form(n, "гость", "гостя", "гостей")


def months_word(n: float | int) -> str:
    return plural_form(n, "месяц", "месяца", "месяцев")


def nights_word(n: float | int) -> str:
    return plural_form(n, "ночь", "ночи", "ночей")


def days_word(n: float | int) -> str:
    return plural_form(n, "день", "дня", "дней")


def _fmt_number(n: float | int) -> str:
    """Drop a trailing .0 so 12.0 месяцев reads as 12 месяцев."""
    if isinstance(n, float) and n.is_integer():
        return str(int(n))
    if isinstance(n, float):
        return f"{n:g}"
    return str(n)


def bedrooms_phrase(n: float | int) -> str:
    return f"{_fmt_number(n)} {bedrooms_word(n)}"


def guests_phrase(n: float | int) -> str:
    return f"{_fmt_number(n)} {guests_word(n)}"


def months_phrase(n: float | int) -> str:
    return f"{_fmt_number(n)} {months_word(n)}"


def nights_phrase(n: float | int) -> str:
    return f"{_fmt_number(n)} {nights_word(n)}"
