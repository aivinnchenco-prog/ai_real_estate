#!/usr/bin/env python3
"""Unified listing text parser → structured fields for Notion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from housing_type import detect_housing_type

# Notion «Удобства» keyword → option name
AMENITY_KEYWORDS: list[tuple[str, str]] = [
    (r"парковк|parking|бесплатная парковка", "Бесплатная парковка"),
    (r"балкон|balcon", "Балкон"),
    (r"кондицион|air\s*condition", "Кондиционер"),
    (r"стиральн|washing\s*machine|washer", "Стиральная машина"),
    (r"посудомо|dishwasher", "Посудомойка"),
    (r"лифт|elevator", "Лифт"),
    (r"охрана\s*24|24/7|guard", "Охрана 24/7"),
    (r"бассейн|pool", "Бассейн"),
    (r"wi[-‑]?fi|wifi|интернет|internet", "Wi-Fi"),
    (r"оборудованн(?:ая|ой)\s+кухн|fully\s+equipped\s+kitchen|full\s+kitchen", "Полностью оборудованная кухня"),
    (r"smart[-\s]?tv|smart\s+tv", "Smart TV"),
    (r"фитнес|gym|fitness", "Фитнес-зал"),
    (r"коворкинг|рабоч(?:ая|ие)\s+зон|workspace|cowork", "Рабочая зона (коворкинг)"),
    (r"детск(?:ие|ая)\s+(?:игров|комнат)|children|playroom", "Детские игровые комнаты"),
    (r"горк|slide", "Горки"),
    (r"sauna|саун", "Sauna"),
    (r"джакузи|jacuzzi|hot\s+tub", "Джакузи"),
    (r"душ\s+на\s+улице|outdoor\s+shower", "Душ на улице"),
    (r"барбекю|bbq|barbecue", "Барбекю"),
    (r"несколько\s+бассейн|multiple\s+pools", "Несколько бассейнов"),
    (r"панорамн.*бассейн", "Панорамный бассейн"),
]

VIEW_RULES: list[tuple[str, str]] = [
    (r"вид\s+на\s+море|sea\s+view|ocean\s+view", "Море"),
    (r"вид\s+на\s+бассейн|pool\s+view", "Бассейн"),
    (r"вид\s+на\s+гор|mountain\s+view", "Горы"),
    (r"вид\s+на\s+сад|garden\s+view", "Сад"),
    (r"вид\s+на\s+город|city\s+view", "Город"),
    (r"вид\s+на\s+парк|park\s+view", "Парк"),
    (r"бассейн|pool\s+view", "Бассейн"),
]


@dataclass
class ListingDraft:
    title: str
    district: str
    rooms: int | None = None
    bathrooms: int | None = None
    area: float | None = None
    price_monthly: float | None = None
    price_yearly: float | None = None
    deposit: float | None = None
    housing_type: str | None = None
    view: str | None = None
    rent_type: str | None = None
    amenities: list[str] = field(default_factory=list)
    source_note: str | None = None
    # Вместимость гостей, явно указанная хозяином в тексте («до 5 гостей»,
    # «sleeps 6»). None = в тексте не указано (фолбэк-формула — в description_validator).
    max_guests: int | None = None


def extract_title(description: str) -> str:
    for line in description.splitlines():
        line = line.strip()
        if len(line) > 5:
            return line[:200]
    return "Property"


def extract_district(description: str, districts: list[str]) -> str:
    text = description.lower()
    for d in districts:
        if d.lower() in text:
            return d
    if "phuket" in text or "пхукет" in text:
        return "Phuket"
    return "Phuket, Thailand"


# Курсы по умолчанию; переопределяются блоком "fx" в config/pipeline.json
DEFAULT_FX = {"usd_to_thb": 36.0, "eur_to_thb": 39.0}

# Airbnb/Word часто вставляют NBSP (\xa0) и узкие пробелы между разрядами: «10 000 ฿»
_NUM_SPACE_RE = re.compile(r"[\s\u00a0\u202f\u2007\u2009]+")


def _to_float(raw: str) -> float:
    """Число из строки с обычными/неразрывными пробелами и запятыми."""
    cleaned = _NUM_SPACE_RE.sub("", (raw or "").strip()).replace(",", "")
    return float(cleaned)


def _parse_deposit(text: str, fx: dict | None = None) -> float | None:
    """Залог всегда в THB. Если в описании сумма в USD/EUR — конвертируем по курсу."""
    m = re.search(
        r"(?:deposit|депозит|залог)\s*[:\s]*([$€฿]?)\s*(\d[\d\s\u00a0\u202f,]*)\s*(usd|\$|eur|€|thb|฿|бат|baht)?",
        text,
        re.I,
    )
    if not m:
        return None
    amount = _to_float(m.group(2))
    currency = ((m.group(1) or "") + (m.group(3) or "")).strip().lower()
    rates = {**DEFAULT_FX, **(fx or {})}
    if currency in ("usd", "$"):
        return float(round(amount * float(rates["usd_to_thb"]), -2))
    if currency in ("eur", "€"):
        return float(round(amount * float(rates["eur_to_thb"]), -2))
    return amount


_MAX_GUESTS_RES = [
    re.compile(r"(?:до|max\.?|maximum|up\s+to)\s+(\d{1,2})\s+(?:гост|guest|человек|чел\b|people|persons?)", re.I),
    re.compile(r"sleeps\s+(\d{1,2})", re.I),
    re.compile(r"accommodates?\s+(?:up\s+to\s+)?(\d{1,2})", re.I),
    re.compile(r"(?:вмеща\w+|размеща\w+)\s+(?:до\s+)?(\d{1,2})", re.I),
    re.compile(r"(?:гостей|guests|вместимость)\s*[:—-]?\s*(\d{1,2})", re.I),
    re.compile(r"(\d{1,2})\s+(?:гост(?:я|ей)|guests)\b", re.I),
]


def _parse_max_guests(text: str) -> int | None:
    """Вместимость гостей, только если хозяин указал её явно в тексте."""
    for pattern in _MAX_GUESTS_RES:
        m = pattern.search(text)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 30:
                return n
    return None


def _parse_rent_type(text: str, source: str) -> str | None:
    combined = f"{text}\n{source}".lower()
    if re.search(r"airbnb|booking\.com|посуточ|краткоср|short[-\s]?term|daily\s+rent", combined):
        return "Краткосрочная"
    if re.search(r"долгоср|long[-\s]?term|от\s+\d+\s+мес|minimum\s+\d+\s+month|на\s+год", combined):
        return "Долгосрочная"
    return None


def _parse_view(text: str) -> str | None:
    lower = text.lower()
    for pattern, name in VIEW_RULES:
        if re.search(pattern, lower, re.I):
            return name
    return None


def _parse_amenities(text: str) -> list[str]:
    lower = text.lower()
    found: list[str] = []
    for pattern, name in AMENITY_KEYWORDS:
        if re.search(pattern, lower, re.I) and name not in found:
            found.append(name)
    if re.search(r"вид\s+на\s+бассейн|pool\s+view", lower, re.I) and "Вид на бассейн" not in found:
        found.append("Вид на бассейн")
    return found


def parse_listing(
    description: str,
    *,
    districts: list[str],
    known_projects: list[str] | None = None,
    complex_name: str | None = None,
    source: str = "",
    fx: dict | None = None,
) -> ListingDraft:
    title = extract_title(description)
    district = extract_district(description, districts)

    rooms = bathrooms = area = price_monthly = price_yearly = None
    # «3 спальни», «3 спален», «3 спальнями», «2BR», «2 bedrooms», «3 комнаты»
    m = re.search(r"(\d+)\s*(?:BR\b|комнат\w*|bedroom\w*|спал\w+)", description, re.I)
    if m:
        rooms = int(m.group(1))
    # «3 ванные», «2 bathrooms», «2 сан.узла»
    m = re.search(r"(\d+)\s*(?:ванн\w*|bathroom\w*|сан\.?\s*узл\w*)", description, re.I)
    if m:
        bathrooms = int(m.group(1))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:m²|m2|кв\.?\s*м|sqm)", description, re.I)
    if m:
        area = float(m.group(1).replace(",", "."))
    m = re.search(
        r"(\d[\d\s\u00a0\u202f,]*)\s*(?:THB|฿|бат)(?!.*(?:год|year))",
        description,
        re.I,
    )
    if m:
        price_monthly = _to_float(m.group(1))
    m = re.search(
        r"(\d[\d\s\u00a0\u202f,]*)\s*(?:THB|฿).*(?:год|year)",
        description,
        re.I,
    )
    if m:
        price_yearly = _to_float(m.group(1))

    housing_type = detect_housing_type(
        description,
        title=title,
        complex_name=complex_name,
        known_projects=known_projects,
    )

    source_note = None
    if source and not re.search(r"https?://", source):
        source_note = source.strip()[:500]

    return ListingDraft(
        title=title,
        district=district,
        rooms=rooms,
        bathrooms=bathrooms,
        area=area,
        price_monthly=price_monthly,
        price_yearly=price_yearly,
        deposit=_parse_deposit(description, fx=fx),
        housing_type=housing_type,
        view=_parse_view(description),
        rent_type=_parse_rent_type(description, source),
        amenities=_parse_amenities(description),
        source_note=source_note,
        max_guests=_parse_max_guests(description),
    )
