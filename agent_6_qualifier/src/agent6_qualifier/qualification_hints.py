"""Deterministic qualification hints — same semantics as archive Gemini prompt.

Archive prompt contract (brain._EXTRACT_PROMPT):
- «на год» / duration → stay_months
- dates without year → nearest future
- only explicit facts

These hints fill gaps when LLM is unavailable or returns {}.
They never overwrite keys already present in the LLM update.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

_YEAR_RE = re.compile(
    r"(?i)(?:\bна\s+год\b|\bна\s+1\s*год\b|\b1\s*год\b|\bone\s+year\b|\ba\s+year\b|"
    r"\bна\s+12\s*мес|\b12\s*месяц)"
)
_HALF_YEAR_RE = re.compile(
    r"(?i)(?:\bна\s+полгода\b|\bполгода\b|\b6\s*мес|\bна\s+6\s*мес|\bhalf\s*year\b)"
)
_THROUGH_DAYS_RE = re.compile(
    r"(?i)через\s+(\d{1,3})\s*(?:дн|день|дня|дней|day|days)\b"
)
_DMY_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")
_BUDGET_RE = re.compile(
    r"(?i)(?:бюджет[^\d]{0,12}|budget[^\d]{0,12})(\d[\d\s]{2,9})\s*(?:к|k|тыс|000)?"
)
_GUESTS_RE = re.compile(
    r"(?i)(?:нас\s+)?(\d+)\s*(?:человек|чел|гост|people|guests|двое|трое)\b|"
    r"\bдвое\b|\bтрое\b|\bчетверо\b"
)
_BEDROOMS_RE = re.compile(
    r"(?i)(\d+)\s*(?:спальн\w*|bedroom\w*|br)\b"
)


def _nearest_future(day: int, month: int, today: date) -> date | None:
    try:
        cand = date(today.year, month, day)
    except ValueError:
        return None
    if cand < today:
        try:
            cand = date(today.year + 1, month, day)
        except ValueError:
            return None
    return cand


def parse_flexible_date(raw: str, *, today: date | None = None) -> date | None:
    """Parse ISO / DD.MM / DD.MM.YYYY into a date (nearest future if year omitted)."""
    if not raw:
        return None
    text = str(raw).strip()
    today = today or date.today()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    m = _DMY_RE.fullmatch(text) or _DMY_RE.search(text)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year_raw = m.group(3)
    if year_raw:
        year = int(year_raw)
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return _nearest_future(day, month, today)


def qualification_hints_from_text(
    message: str, *, today: date | None = None
) -> dict[str, Any]:
    """Explicit local facts only — mirrors archive extract schema keys."""
    today = today or date.today()
    text = message or ""
    out: dict[str, Any] = {}

    if _YEAR_RE.search(text):
        out["stay_months"] = 12.0
    elif _HALF_YEAR_RE.search(text):
        out["stay_months"] = 6.0

    m = _THROUGH_DAYS_RE.search(text)
    if m:
        days = int(m.group(1))
        out["check_in"] = (today + timedelta(days=days)).isoformat()

    # Standalone / clear DD.MM date (avoid matching random numbers in object ids)
    for m in _DMY_RE.finditer(text):
        # skip if looks like part of object id YYYYMMDD
        start = m.start()
        if start > 0 and text[start - 1].isalnum():
            continue
        parsed = parse_flexible_date(m.group(0), today=today)
        if parsed and "check_in" not in out:
            out["check_in"] = parsed.isoformat()
            break

    bm = _BUDGET_RE.search(text)
    if bm:
        digits = re.sub(r"\D", "", bm.group(1))
        if digits:
            val = float(digits)
            if val < 1000:  # «150к»
                val *= 1000
            out["budget"] = val

    if re.search(r"(?i)\bдвое\b", text):
        out["guests"] = 2
    elif re.search(r"(?i)\bтрое\b", text):
        out["guests"] = 3
    else:
        gm = _GUESTS_RE.search(text)
        if gm and gm.group(1):
            out["guests"] = int(gm.group(1))

    br = _BEDROOMS_RE.search(text)
    if br:
        out["bedrooms"] = int(br.group(1))

    return out


def merge_hints(llm_update: dict, hints: dict) -> dict:
    """LLM wins on conflict; hints only fill missing keys."""
    merged = dict(llm_update or {})
    for key, value in (hints or {}).items():
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = value
    return merged
