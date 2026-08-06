"""Район для Marketplace из ссылки Google Maps (Notion «Google Maps»)."""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

# Plus code в начале строки: X8R4+HWJ Choeng Thale, ...
_PLUS_CODE_PREFIX_RE = re.compile(r"^[A-Z0-9]{4,}\+[A-Z0-9]{2,4}\s+", re.IGNORECASE)
_PLUS_CODE_LABEL_RE = re.compile(r"^plus\s*code:\s*", re.IGNORECASE)


def parse_district_from_maps_label(label: str) -> str | None:
    """
    «X8R4+HWJ Choeng Thale, Thalang District, Пхукет» → «Choeng Thale».
    Plus code отбрасывается, берётся первый сегмент до запятой.
    """
    text = (label or "").strip()
    if not text:
        return None
    text = _PLUS_CODE_LABEL_RE.sub("", text).strip()
    text = _PLUS_CODE_PREFIX_RE.sub("", text).strip()
    if not text:
        return None
    return text.split(",")[0].strip() or None


def coords_from_maps_url(url: str | None) -> tuple[float, float] | None:
    if not url:
        return None
    m = re.search(r"[?&]q=(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", url)
    if not m:
        m = re.search(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)", url)
    if not m:
        return None
    lat, lng = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def place_from_maps_url_path(url: str | None) -> str | None:
    """/maps/place/Choeng+Thale,+Thalang/... → Choeng Thale."""
    if not url:
        return None
    m = re.search(r"/maps/place/([^/@?]+)", url)
    if not m:
        return None
    raw = urllib.parse.unquote(m.group(1).replace("+", " "))
    return parse_district_from_maps_label(raw)


def query_from_maps_search_url(url: str | None) -> str | None:
    """/maps/search/Choeng%20Thale%2C%20Phuket → Choeng Thale."""
    if not url:
        return None
    m = re.search(r"/maps/search/([^/?#]+)", url)
    if not m:
        return None
    raw = urllib.parse.unquote(m.group(1).replace("+", " "))
    return parse_district_from_maps_label(raw)


def query_from_maps_q_text(url: str | None) -> str | None:
    """?q=Choeng+Thale (не координаты) → Choeng Thale."""
    if not url:
        return None
    m = re.search(r"[?&]q=([^&]+)", url)
    if not m:
        return None
    raw = urllib.parse.unquote(m.group(1).replace("+", " ")).strip()
    if re.fullmatch(r"-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+", raw):
        return None
    return parse_district_from_maps_label(raw)


def reverse_geocode_district_nominatim(lat: float, lng: float) -> str | None:
    """Бесплатный reverse geocode без API-ключа (OpenStreetMap Nominatim)."""
    params = urllib.parse.urlencode(
        {
            "lat": f"{lat:.6f}",
            "lon": f"{lng:.6f}",
            "format": "json",
            "accept-language": "en",
            "zoom": "14",
        }
    )
    url = f"https://nominatim.openstreetmap.org/reverse?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "publisher-social-fb-marketplace/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    address = data.get("address") or {}
    for key in (
        "suburb",
        "town",
        "village",
        "city_district",
        "county",
        "municipality",
        "locality",
    ):
        value = (address.get(key) or "").strip()
        if value:
            return value
    display = (data.get("display_name") or "").strip()
    return parse_district_from_maps_label(display)


def reverse_geocode_district(lat: float, lng: float, *, language: str = "en") -> str | None:
    """Координаты → район: сначала Nominatim (без ключа), затем Google API если есть ключ."""
    district = reverse_geocode_district_nominatim(lat, lng)
    if district:
        return district

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return None
    params = urllib.parse.urlencode(
        {
            "latlng": f"{lat},{lng}",
            "key": api_key,
            "language": language,
            "result_type": "sublocality|locality|administrative_area_level_2",
        }
    )
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    results = data.get("results") or []
    if not results:
        return None
    preferred = (
        ("sublocality_level_1", "sublocality"),
        ("locality",),
        ("administrative_area_level_2",),
    )
    components = results[0].get("address_components", [])
    for types in preferred:
        for comp in components:
            if any(t in comp.get("types", []) for t in types):
                name = (comp.get("long_name") or "").strip()
                if name:
                    return name
    formatted = (results[0].get("formatted_address") or "").strip()
    return parse_district_from_maps_label(formatted)


def district_from_google_maps_url(url: str | None) -> str | None:
    """Район из Notion-ссылки Google Maps (без обязательного API-ключа)."""
    for parser in (
        place_from_maps_url_path,
        query_from_maps_search_url,
        query_from_maps_q_text,
    ):
        district = parser(url)
        if district:
            return district
    coords = coords_from_maps_url(url)
    if coords:
        return reverse_geocode_district(*coords)
    return None


def marketplace_location_query(
    *,
    google_maps_url: str | None,
    district_fallback: str | None,
) -> str:
    """Текст для поиска на карте FB Marketplace."""
    district = district_from_google_maps_url(google_maps_url)
    if not district:
        district = (district_fallback or "").strip()
    if not district:
        return ""
    low = district.lower()
    if "phuket" in low or "пхукет" in low or "ภูเก็ต" in district:
        return district
    return f"{district}, Phuket"
