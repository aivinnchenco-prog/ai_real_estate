#!/usr/bin/env python3
"""
Google Maps resolver for Agent 2.

Если в описании есть название проекта/комплекса — ищем его на карте.
Если нет — fallback по строке локации (📍) или району.

При наличии GOOGLE_MAPS_API_KEY использует Places API для точной ссылки.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

# Паттерны названий ЖК на Phuket (The Title, Skypark, …)
COMPLEX_REGEXES = [
    r"(?:The\s+)?Title\s+(?:Legendary|Serenity|Crescent|Residence|Resident|Artrio|Vivi|Katani|Layan|Nora|Coral|Heritage|Baba|Cote|Vi)\b[\w\s\-]*",
    r"Legendary\s+Residen(?:ce|t)\s+Title\b[\w\s\-]*",
    r"Skypark\s+[A-Za-z][\w\s\-]*",
    r"MontAzure[\w\s\-]*",
    r"Angsana[\w\s\-]*",
    r"Laguna[\w\s\s\-]*(?:Homes|Park|Village|Beach)?",
    r"Clover\b[\w\s\-]*",
    r"Botanica[\w\s\-]*",
    r"Andara[\w\s\-]*",
    r"Trichada[\w\s\-]*",
    r"Sunshine[\w\s\-]*",
]

# Центр Phuket для location bias
PHUKET_BIAS = "circle:45000@7.8804,98.3923"

# Если у объявления есть координаты карты, результат Places API принимаем
# только когда найденное место не дальше этого радиуса от них (Airbnb
# размывает точку до ~500 м). Иначе поиск по названию может увести
# ссылку на другой конец района.
PLACE_MATCH_MAX_KM = 1.0

# Центроиды районов Пхукета — офлайн-определение района по координатам объявления
PHUKET_DISTRICT_CENTROIDS: dict[str, tuple[float, float]] = {
    "Mai Khao": (8.1440, 98.3070),
    "Nai Yang": (8.0910, 98.3010),
    "Thalang": (8.0330, 98.3390),
    "Layan": (8.0170, 98.2960),
    "Bang Tao": (7.9925, 98.2957),
    "Laguna": (7.9887, 98.2997),
    "Choeng Thale": (7.9800, 98.3050),
    "Surin": (7.9770, 98.2790),
    "Kamala": (7.9530, 98.2830),
    "Kathu": (7.9174, 98.3324),
    "Patong": (7.8965, 98.2966),
    "Phuket Town": (7.8804, 98.3923),
    "Karon": (7.8460, 98.2940),
    "Chalong": (7.8264, 98.3390),
    "Kata": (7.8200, 98.2980),
    "Cape Panwa": (7.8060, 98.4030),
    "Rawai": (7.7717, 98.3269),
    "Nai Harn": (7.7770, 98.3050),
}

# Generic-значения района, которые можно перекрывать районом из координат
_GENERIC_DISTRICTS = {"phuket", "phuket, thailand", "пхукет"}


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math

    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def district_from_coords(lat: float, lng: float, max_km: float = 25.0) -> str | None:
    """Ближайший район Пхукета по координатам. None — если точка слишком далеко."""
    best_name, best_dist = None, float("inf")
    for name, (clat, clng) in PHUKET_DISTRICT_CENTROIDS.items():
        d = _haversine_km(lat, lng, clat, clng)
        if d < best_dist:
            best_name, best_dist = name, d
    if best_dist > max_km:
        return None
    return best_name


def coords_point_url(lat: float, lng: float) -> str:
    """Ссылка на точку в Google Maps по координатам."""
    return f"https://www.google.com/maps?q={lat:.6f},{lng:.6f}"


@dataclass
class MapsResult:
    url: str
    query: str
    complex_name: str | None
    district: str
    address: str
    method: str  # places_api | search_url
    place_name: str | None = None
    place_id: str | None = None


def _clean_name(s: str) -> str:
    s = re.sub(r"\*+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" \t—–-|,")


def extract_location_line(description: str) -> str | None:
    """Строка после 📍 или 'Локация:'"""
    for line in description.splitlines():
        line = line.strip()
        if line.startswith("📍"):
            return _clean_name(line.lstrip("📍").strip())
        m = re.match(r"(?:локация|location)\s*:\s*(.+)", line, re.I)
        if m:
            return _clean_name(m.group(1))
    return None


def _trim_complex_name(name: str) -> str:
    """Обрезает шум из заголовка объявления (2BR, «с видом», и т.д.)."""
    stop = re.search(
        r"\b(\d+\s*br|1br|2br|3br|studio|студия|с\s+видом|for\s+rent|аренда|/|\|)",
        name,
        re.I,
    )
    if stop:
        name = name[: stop.start()]
    return _clean_name(name)


def _score_complex_name(name: str) -> int:
    lower = name.lower()
    score = len(name)
    if lower.startswith("the title"):
        score += 50
    if "bang-tao" in lower or "bang tao" in lower:
        score += 40
    if any(k in lower for k in ("skypark", "clover", "montazure", "laguna", "residence")):
        score += 20
    if re.search(r"\b\d+\s*br\b", lower):
        score -= 30
    if "с видом" in lower or "вид на" in lower:
        score -= 20
    return score


def extract_complex_name(description: str, known_projects: list[str] | None = None) -> str | None:
    """
    Извлекает название проекта/комплекса, если оно есть в тексте.
    Возвращает None, если проект не распознан.
    """
    text = description.replace("\n", " ")
    candidates: list[str] = []

    for pattern in COMPLEX_REGEXES:
        for m in re.finditer(pattern, text, re.I):
            name = _trim_complex_name(m.group(0))
            if len(name) >= 3:
                candidates.append(name[:120])

    if candidates:
        return max(candidates, key=_score_complex_name)

    if known_projects:
        lower = text.lower()
        for proj in sorted(known_projects, key=len, reverse=True):
            if proj.lower() in lower:
                idx = lower.find(proj.lower())
                tail = text[idx : idx + len(proj) + 40]
                m = re.match(rf"({re.escape(proj)}[\w\s\-]*)", tail, re.I)
                if m:
                    return _trim_complex_name(m.group(1))[:120]
                return proj

    # 🏢 **The Title Legendary ...**
    m = re.search(r"🏢\s*\*{0,2}([^*\n—–]+?)(?:\*{0,2}|—|–|$)", description)
    if m:
        candidate = _clean_name(m.group(1))
        if _looks_like_complex(candidate):
            return candidate[:120]

    # «Комплекс: …» / «Project: …»
    m = re.search(r"(?:комплекс|project|жк|condo|residence)\s*:\s*([^\n]+)", description, re.I)
    if m:
        candidate = _clean_name(m.group(1))
        if len(candidate) >= 3:
            return candidate[:120]

    return None


def _looks_like_complex(name: str) -> bool:
    """Отсеивает generic заголовки без названия проекта."""
    lower = name.lower()
    generic = (
        "studio", "студия", "apartment", "квартира", "condo", "house", "villa",
        "2br", "3br", "1br", "bedroom", "for rent", "аренда",
    )
    if any(g in lower for g in generic) and not any(
        k in lower for k in ("title", "skypark", "legendary", "clover", "montazure", "laguna")
    ):
        return False
    return len(name) >= 4


def build_search_query(
    *,
    complex_name: str | None,
    district: str,
    location_line: str | None,
    title: str,
) -> str:
    """Собирает поисковый запрос для Google Maps."""
    if complex_name:
        return f"{complex_name} {district} Phuket Thailand"

    if location_line and len(location_line) > 8:
        if "phuket" not in location_line.lower():
            return f"{location_line} Phuket Thailand"
        return location_line

    # Без проекта — не тащим весь заголовок (часто шум)
    if _looks_like_complex(title):
        return f"{title} {district} Phuket Thailand"

    return f"{district} Phuket Thailand"


def _search_url(query: str) -> str:
    return f"https://www.google.com/maps/search/{urllib.parse.quote(query)}"


def _place_card_url(place_id: str, name: str | None = None) -> str:
    """Ссылка на карточку места в Google Maps (не голая точка по координатам)."""
    if name:
        return (
            "https://www.google.com/maps/search/?api=1"
            f"&query={urllib.parse.quote(name)}"
            f"&query_place_id={place_id}"
        )
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def _place_details(place_id: str, api_key: str) -> dict[str, Any] | None:
    params = urllib.parse.urlencode(
        {
            "place_id": place_id,
            "fields": "url,name,formatted_address,place_id",
            "key": api_key,
        }
    )
    url = f"https://maps.googleapis.com/maps/api/place/details/json?{params}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    status = data.get("status")
    if status != "OK":
        return None
    return data.get("result")


def _places_api_resolve(query: str, api_key: str) -> dict[str, Any] | None:
    params = urllib.parse.urlencode(
        {
            "input": query,
            "inputtype": "textquery",
            "fields": "formatted_address,name,geometry,place_id",
            "key": api_key,
            "locationbias": PHUKET_BIAS,
        }
    )
    url = f"https://maps.googleapis.com/maps/api/place/findplacefromtext/json?{params}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    status = data.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(f"Places API: {status} — {data.get('error_message', '')}")

    candidates = data.get("candidates") or []
    if not candidates:
        return None

    c = candidates[0]
    place_id = c.get("place_id")
    place_name = c.get("name")
    if not place_id:
        return None

    loc = (c.get("geometry") or {}).get("location") or {}
    location = (loc.get("lat"), loc.get("lng")) if loc.get("lat") is not None else None

    # Каноническая ссылка на карточку проекта (как maps.app.goo.gl / cid=…)
    details = _place_details(place_id, api_key)
    if details and details.get("url"):
        place_url = details["url"]
        place_name = details.get("name") or place_name
        formatted_address = details.get("formatted_address") or c.get("formatted_address")
    else:
        place_url = _place_card_url(place_id, place_name)
        formatted_address = c.get("formatted_address")

    return {
        "url": place_url,
        "place_id": place_id,
        "place_name": place_name,
        "formatted_address": formatted_address,
        "location": location,
    }


def build_address(
    district: str,
    complex_name: str | None,
    location_line: str | None,
    place_address: str | None,
) -> str:
    if place_address:
        return place_address
    if location_line:
        return location_line
    if complex_name:
        return f"{complex_name}, {district}, Phuket, Thailand"
    return f"{district}, Phuket, Thailand"


def resolve_google_maps(
    description: str,
    district: str,
    title: str,
    *,
    known_projects: list[str] | None = None,
    api_key: str | None = None,
    coords: tuple[float, float] | None = None,
) -> MapsResult:
    complex_name = extract_complex_name(description, known_projects)
    location_line = extract_location_line(description)

    # Координаты с карты объявления (Airbnb/FB) — самый надёжный источник:
    # точка на карте + район по ближайшему центроиду.
    if coords:
        coord_district = district_from_coords(*coords)
        if coord_district:
            district = coord_district

    query = build_search_query(
        complex_name=complex_name,
        district=district,
        location_line=location_line,
        title=title,
    )

    method = "search_url"
    url = _search_url(query)
    if coords:
        url = coords_point_url(*coords)
        method = "coords_point"
    place_name = None
    place_address = None
    place_id = None

    key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
    if key:
        try:
            resolved = _places_api_resolve(query, key)
            if resolved:
                # Найденное по названию место используем только если оно
                # рядом с координатами объявления (или координат нет вовсе);
                # иначе точка с карты Airbnb/FB надёжнее текстового поиска.
                place_loc = resolved.get("location")
                near_coords = (
                    coords is None
                    or place_loc is None
                    or _haversine_km(*coords, *place_loc) <= PLACE_MATCH_MAX_KM
                )
                if near_coords:
                    url = resolved["url"]
                    place_name = resolved.get("place_name")
                    place_id = resolved.get("place_id")
                    place_address = resolved.get("formatted_address")
                    method = "places_api"
        except (urllib.error.URLError, RuntimeError, json.JSONDecodeError):
            pass

    address = build_address(district, complex_name, location_line, place_address)

    return MapsResult(
        url=url,
        query=query,
        complex_name=complex_name,
        district=district,
        address=address,
        method=method,
        place_name=place_name,
        place_id=place_id,
    )
