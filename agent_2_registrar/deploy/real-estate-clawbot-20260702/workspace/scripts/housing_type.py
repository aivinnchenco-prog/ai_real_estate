#!/usr/bin/env python3
"""Определение типа жилья из описания объявления."""

from __future__ import annotations

import re

VILLA_PATTERNS = (
    r"\bvilla\b",
    r"\bвилла\b",
    r"pool\s+villa",
    r"private\s+villa",
    r"вилла\s+с\s+бассейном",
    r"отдельн(?:ый|ая|ого)\s+(?:дом|вилл)",
)

CONDO_PATTERNS = (
    r"\bcondo(?:minium)?\b",
    r"\bкондо(?:мин(?:иум|иум)?)?\b",
    r"кондоминиум",
    r"курортн(?:ый|ого)\s+комплекс",
    r"resort\s+complex",
    r"\bresidence\b",
    r"apartment\s+complex",
    r"жил(?:ой|ого)\s+комплекс",
    r"the\s+title",
    r"skypark",
    r"montazure",
    r"botanica",
    r"angsana",
)

STUDIO_PATTERNS = (r"\bstudio\b", r"\bстудия\b", r"\b0\s*br\b")

TOWNHOUSE_PATTERNS = (r"town\s*house", r"таун\s*хаус", r"таунhaus")

ROOM_PATTERNS = (r"\broom\s+for\s+rent\b", r"комната\s+в\s+", r"shared\s+apartment", r"сдаётся\s+комната")

APARTMENT_PATTERNS = (r"\bквартира\b", r"\bapartment\b", r"\bflat\b")

HOUSE_PATTERNS = (r"\bчастный\s+дом\b", r"\bdetached\s+house\b", r"\bprivate\s+house\b")


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def detect_housing_type(
    description: str,
    *,
    title: str = "",
    complex_name: str | None = None,
    known_projects: list[str] | None = None,
) -> str | None:
    """
    Возвращает значение для Notion select «Тип жилья» или None.
    """
    combined = "\n".join(filter(None, [title, description, complex_name])).lower()

    if _matches(combined, VILLA_PATTERNS) and not re.search(r"villa\s+market", combined, re.I):
        return "Вилла"

    if _matches(combined, STUDIO_PATTERNS):
        return "Студия"

    if _matches(combined, TOWNHOUSE_PATTERNS):
        return "Таунхаус"

    if _matches(combined, ROOM_PATTERNS):
        return "Комната"

    if _matches(combined, CONDO_PATTERNS):
        return "Кондоминиум"

    if known_projects:
        for proj in sorted(known_projects, key=len, reverse=True):
            if proj.lower() in combined:
                return "Кондоминиум"

    if _matches(combined, APARTMENT_PATTERNS):
        if re.search(r"\d+\s*br|спален|bedroom|комплекс|complex|residence", combined, re.I):
            return "Кондоминиум"
        return "Квартира"

    # 2BR / 3BR в заголовке без villa — типичная квартира в ЖК на Пхукете
    if re.search(r"\d+\s*br\b", combined, re.I):
        return "Кондоминиум"

    if _matches(combined, HOUSE_PATTERNS):
        return "Дом"

    return None
