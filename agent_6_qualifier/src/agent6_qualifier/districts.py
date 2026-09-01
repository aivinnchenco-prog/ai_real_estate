"""Granular Phuket district aliases for Agent 6 (Notion «Район»).

Do NOT import Agent 2 ``zones_mapping.json``: that map collapses Kata→Karon
and Layan/Laguna→Bang Tao. Qualifier listings store the granular names.
"""
from __future__ import annotations

import re
from typing import Iterable

_SPACE_RE = re.compile(r"\s+")

# Exact Notion «Район» values (latin, as stored).
CANONICAL_DISTRICTS: frozenset[str] = frozenset({
    "Rawai",
    "Kata",
    "Karon",
    "Layan",
    "Laguna",
    "Thalang",
    "Choeng Thale",
    "Bang Tao",
    "Kamala",
    "Chalong",
    "Surin",
    "Nai Harn",
    "Mai Khao",
    "Patong",
    "Si Sunthon",
    "Kathu",
})

# Normalized key (lower/trim/collapsed spaces) → granular Notion name.
DISTRICT_ALIASES: dict[str, str] = {
    "rawai": "Rawai",
    "равай": "Rawai",
    "равайи": "Rawai",
    "раваи": "Rawai",
    "kata": "Kata",
    "ката": "Kata",
    "karon": "Karon",
    "карон": "Karon",
    "layan": "Layan",
    "лаян": "Layan",
    "laguna": "Laguna",
    "лагуна": "Laguna",
    "thalang": "Thalang",
    "тхаланг": "Thalang",
    "таланг": "Thalang",
    "choeng thale": "Choeng Thale",
    "choeng talay": "Choeng Thale",
    "чоенг тале": "Choeng Thale",
    "чоенг талай": "Choeng Thale",
    "чернг талай": "Choeng Thale",
    "bang tao": "Bang Tao",
    "bangtao": "Bang Tao",
    "банг тао": "Bang Tao",
    "бангтао": "Bang Tao",
    "kamala": "Kamala",
    "камала": "Kamala",
    "chalong": "Chalong",
    "чалонг": "Chalong",
    "surin": "Surin",
    "сурин": "Surin",
    "nai harn": "Nai Harn",
    "най харн": "Nai Harn",
    "най хар": "Nai Harn",
    "mai khao": "Mai Khao",
    "май кхао": "Mai Khao",
    "patong": "Patong",
    "патонг": "Patong",
    "si sunthon": "Si Sunthon",
    "си сунтон": "Si Sunthon",
    "kathu": "Kathu",
    "кату": "Kathu",
}

# Tokens stripped from extracted district lists (not written to the profile).
_FILTER_TOKENS = frozenset({
    "или иные",
    "иные",
    "любой",
    "любые",
    "подойдет любой",
    "подойдёт любой",
    "предпочтительно",
    "другой",
    "другие",
    "any",
    "any area",
    "any district",
})

# Explicit «any district» consent — disables the hard district filter.
_ANY_DISTRICT_RE = re.compile(
    r"(?i)(?:\bили\s+иные\b|\bиные\b|"
    r"подойд[её]т\s+любой|"
    r"\bлюбой\b|\bлюбые\b|"
    r"\bany\s+(?:area|district|place)\b)",
)


def normalize_key(value: str | None) -> str:
    return _SPACE_RE.sub(" ", (value or "").strip().lower())


def is_filter_token(value: str | None) -> bool:
    return normalize_key(value) in _FILTER_TOKENS


def is_any_district_phrase(text: str | None) -> bool:
    return bool(text and _ANY_DISTRICT_RE.search(text))


def canonicalize_district(value: str | None) -> str | None:
    """Map a client/listing district token to a Notion «Район» name.

    Unresolvable and filter tokens return None (do not write).
    Latin canonical names pass through with canonical casing.
    """
    key = normalize_key(value)
    if not key or key in _FILTER_TOKENS:
        return None
    if key in DISTRICT_ALIASES:
        return DISTRICT_ALIASES[key]
    return None


def apply_districts_update(
    lead,
    raw_districts: Iterable[str] | None,
    *,
    message: str = "",
) -> None:
    """Write canonical EN districts; set ``any_district`` on explicit 'any'."""
    tokens = list(raw_districts or [])
    if is_any_district_phrase(message) or any(is_filter_token(t) for t in tokens):
        lead.any_district = True
    if raw_districts is None:
        return
    for raw in tokens:
        canon = canonicalize_district(raw)
        if canon and canon not in lead.districts:
            lead.districts.append(canon)
