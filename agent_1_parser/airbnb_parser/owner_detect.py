"""Распознавание владельца/агентства по листингу Airbnb.

Источники (по убыванию приоритета):
  1. Профиль хозяина — «Моя профессия / Моя професія / My work: …»
     (hostHighlights из секции MEET_YOUR_HOST) и текст «о себе».
  2. Описание объявления — название компании/агентства.

Дальше — веб-поиск «<компания> Phuket» (DuckDuckGo) и извлечение контактов
(телефоны, email, сайты) из сниппетов результатов.

Всё опционально деградирует: без интернета/зависимостей возвращаем то,
что удалось распознать локально.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from CustomLogger import logger

# Явные маркеры «это компания, а не человек»
_COMPANY_SUFFIXES = re.compile(
    r"\b(co\.?,?\s*ltd\.?|ltd\.?|limited|llc|inc\.?|pte|плc|plc)\b", re.I
)
_COMPANY_KEYWORDS = re.compile(
    r"\b(group|realty|property|properties|estate|estates|agency|management|"
    r"hospitality|rentals?|villas?|resort|development|consult\w*|invest\w*|"
    r"группа|агентство|агенція|компания|компанія|управляющая|управлінська|нерухомість|недвижимость)\b",
    re.I,
)
# «Моя профессия: …» в разных локалях Airbnb
_PROFESSION_RE = re.compile(
    r"(?:моя\s+профессия|моя\s+професія|my\s+work|профессия|професія|profession)\s*[:：]\s*(.+)",
    re.I,
)
# Компания в свободном тексте: «управляющая группа Atlas One», «agency: X» и т.п.
_INLINE_COMPANY_RES = [
    re.compile(r"(?:управл(?:яющая|інська)\s+(?:группа|група|компания|компанія)|managed\s+by|управляется)\s*[:：]?\s*([A-ZА-ЯЁЇІЄ][\w\s&.'-]{2,60})", re.I),
    re.compile(r"(?:агентство|агенція|agency|company|компания|компанія)\s*[:：]\s*([A-ZА-ЯЁЇІЄ][\w\s&.'-]{2,60})", re.I),
    re.compile(r"\b([A-Z][\w&.'-]+(?:\s+[A-Z][\w&.'-]+){0,4}\s+(?:Group|Realty|Property|Properties|Estate|Agency|Management|Hospitality|Villas?|Rentals?))\b"),
    re.compile(r"\b([A-Z][\w&.'-]+(?:\s+[A-Z][\w&.'-]+){0,4}\s+Co\.?,?\s*Ltd\.?)\b", re.I),
]

_PHONE_RE = re.compile(r"(?:\+66|0066|0)\s?\d{1,2}[\s-]?\d{3}[\s-]?\d{3,4}|\+\d{10,14}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")


def _clean_company(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" \t.,:;—–-|»«\"'")
    # Обрезаем хвост после точки/переноса, если это уже другое предложение
    name = re.split(r"[.\n]", name)[0].strip()
    return name[:80]


def looks_like_company(name: str) -> bool:
    """Название похоже на компанию/агентство, а не на имя человека."""
    name = (name or "").strip()
    if len(name) < 3:
        return False
    if _COMPANY_SUFFIXES.search(name) or _COMPANY_KEYWORDS.search(name):
        return True
    # Несколько капитализированных слов без ключевых — слишком похоже на имя человека
    return False


def company_from_profession(host: dict) -> str | None:
    """«Моя профессия: Управлінська група Atlas One» из хайлайтов/о-себе профиля."""
    texts = list(host.get("highlights") or [])
    if host.get("about"):
        texts.append(host["about"])
    for text in texts:
        m = _PROFESSION_RE.search(text)
        if not m:
            continue
        candidate = _clean_company(m.group(1))
        if candidate and (looks_like_company(candidate) or len(candidate.split()) >= 2):
            return candidate
    return None


def company_from_text(text: str) -> str | None:
    """Название компании/агентства в описании объявления или «о себе»."""
    if not text:
        return None
    for pattern in _INLINE_COMPANY_RES:
        for m in pattern.finditer(text):
            candidate = _clean_company(m.group(1))
            if looks_like_company(candidate):
                return candidate
    return None


def project_region() -> str:
    """Регион для поисковых запросов — из общего config/project.json монорепы."""
    candidate = Path(__file__).resolve().parents[3] / "config" / "project.json"
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        return (data.get("region") or {}).get("search_en") or "Phuket"
    except (OSError, ValueError):
        return "Phuket"


def search_company_contacts(company: str, region: str | None = None, max_results: int = 8) -> dict:
    """Веб-поиск контактов компании: телефоны, email, сайты (DuckDuckGo)."""
    contacts: dict = {"phones": [], "emails": [], "links": []}
    try:
        try:
            from ddgs import DDGS  # новое имя пакета
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError:
        logger.warning("owner_detect: ddgs/duckduckgo_search не установлен — веб-поиск пропущен")
        return contacts

    query = f"{company} {region or project_region()} contact"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        logger.warning(f"owner_detect: веб-поиск не удался: {e}")
        return contacts

    blob_parts = []
    for r in results:
        title = r.get("title", "")
        body = r.get("body", "")
        href = r.get("href", "")
        blob_parts.append(f"{title} {body}")
        if href and company.split()[0].lower() in href.lower():
            contacts["links"].append(href)
    blob = " ".join(blob_parts)

    contacts["phones"] = list(dict.fromkeys(m.group(0).strip() for m in _PHONE_RE.finditer(blob)))[:5]
    contacts["emails"] = list(dict.fromkeys(_EMAIL_RE.findall(blob)))[:5]
    contacts["links"] = list(dict.fromkeys(contacts["links"]))[:5]
    if not contacts["links"] and results:
        contacts["links"] = [r.get("href", "") for r in results[:2] if r.get("href")]

    logger.info(f"owner_detect: «{query}» → phones={len(contacts['phones'])} "
                f"emails={len(contacts['emails'])} links={len(contacts['links'])}")
    return contacts


def detect_owner(description: str, host: dict, *, web_search: bool = True) -> dict:
    """Полный проход: профиль → описание → «о себе» → веб-поиск контактов.

    Возвращает dict для parsed.json:
    {host_name, company, company_source, contacts{phones,emails,links}, avatar_url}
    """
    host = host or {}
    company, source = None, None

    candidate = company_from_profession(host)
    if candidate:
        company, source = candidate, "host_profession"

    if not company:
        candidate = company_from_text(description or "")
        if candidate:
            company, source = candidate, "description"

    if not company and host.get("about"):
        candidate = company_from_text(host["about"])
        if candidate:
            company, source = candidate, "host_about"

    result = {
        "host_name": host.get("name", ""),
        "is_superhost": bool(host.get("is_superhost")),
        "avatar_url": host.get("avatar_url", ""),
        "company": company,
        "company_source": source,
        "contacts": {"phones": [], "emails": [], "links": []},
    }

    if company and web_search:
        result["contacts"] = search_company_contacts(company)

    return result
