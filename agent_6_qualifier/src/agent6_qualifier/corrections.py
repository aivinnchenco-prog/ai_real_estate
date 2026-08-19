"""Explicit correction detection (Wave 3, policy §3).

«Не 150, а 200 тысяч» must update ``budget`` and nothing else. Detection is
deterministic so an LLM extract can never widen a correction into a reset.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import LeadProfile

# «не X, а Y» / «не X, теперь Y» / «X вместо Y» — the shape of a correction.
_CORRECTION_FRAME = re.compile(
    r"(?:^|[\s,.;:!?(])не\s+(?P<old>[^,.;!?]{1,40}?)\s*(?:,|\s)\s*"
    r"(?:а|теперь|давайте|лучше)\s+(?P<new>[^,.;!?]{1,40})",
    re.IGNORECASE,
)
_INSTEAD_FRAME = re.compile(
    r"(?P<new>[^,.;!?]{1,40}?)\s+вместо\s+(?P<old>[^,.;!?]{1,40})",
    re.IGNORECASE,
)
# «я говорил 150» / «я же сказал Раваи» — restating, also an explicit correction.
_RESTATE_FRAME = re.compile(
    r"я\s+(?:же\s+)?(?:уже\s+)?(?:говорил|сказал|писал|имел\s+в\s+виду)",
    re.IGNORECASE,
)

_MONTH_NAMES = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

_BUDGET_HINT = re.compile(
    r"бюджет|тысяч|тыс\b|к\b|тыщ|бат|thb|฿|month|мес", re.IGNORECASE
)
_GUESTS_HINT = re.compile(
    r"нас\b|человек|гост|взросл|заедет|заедем|прожива", re.IGNORECASE
)
_BEDROOMS_HINT = re.compile(r"спал|комнат|bedroom", re.IGNORECASE)
_CHECK_IN_HINT = re.compile(r"заезд|заезжа|засел|приезжа|прилет|check.?in", re.IGNORECASE)
_CHECK_OUT_HINT = re.compile(r"выезд|выезжа|съезжа|check.?out|до\s+\d", re.IGNORECASE)
_STAY_HINT = re.compile(r"срок|месяц|год|аренд\w*\s+на", re.IGNORECASE)
_DISTRICT_HINT = re.compile(r"район|локац|area", re.IGNORECASE)

# Slot → the update keys a correction of that slot is allowed to touch.
_SLOT_KEYS = {
    "budget": ("budget",),
    "guests": ("guests",),
    "bedrooms": ("bedrooms",),
    "check_in": ("check_in",),
    "check_out": ("check_out",),
    "stay_months": ("stay_months",),
    "districts": ("districts",),
}


@dataclass
class CorrectionResult:
    """Which slots the client explicitly corrected in this message."""
    corrected_slots: list[str] = field(default_factory=list)
    target_values: dict[str, Any] = field(default_factory=dict)
    is_explicit: bool = False
    raw_old: str = ""
    raw_new: str = ""

    def __bool__(self) -> bool:
        return bool(self.corrected_slots)

    @property
    def correction_target(self) -> str:
        return self.corrected_slots[0] if self.corrected_slots else ""


def _parse_amount(text: str) -> float | None:
    """«200 тысяч» / «200к» / «200000» → 200000.0."""
    m = re.search(r"(\d[\d\s.,]*)", text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(",", ".").rstrip(".")
    try:
        value = float(raw)
    except ValueError:
        return None
    tail = text[m.end():].lower()
    if re.match(r"\s*(?:тыс|тысяч|тыщ|к\b|k\b)", tail) or (
        value < 1000 and re.search(r"тыс|тысяч|к\b|k\b", text, re.IGNORECASE)
    ):
        value *= 1000
    return value


def _parse_int(text: str) -> int | None:
    m = re.search(r"\d+", text)
    return int(m.group(0)) if m else None


def _parse_day_or_date(text: str, *, today: date | None = None) -> date | None:
    """«15-го» / «15 марта» / «15.10» relative to today."""
    base = today or date.today()
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else base.year
        if year < 100:
            year += 2000
        try:
            parsed = date(year, month, day)
        except ValueError:
            return None
        if m.group(3) is None and parsed < base:
            try:
                parsed = date(year + 1, month, day)
            except ValueError:
                return None
        return parsed

    m = re.search(r"(\d{1,2})\s*(?:-?(?:го|е|ое))?\s+([а-яё]{3,})", text, re.IGNORECASE)
    if m:
        day = int(m.group(1))
        word = m.group(2).lower()
        for stem, month in _MONTH_NAMES.items():
            if word.startswith(stem):
                year = base.year
                try:
                    parsed = date(year, month, day)
                except ValueError:
                    return None
                if parsed < base:
                    try:
                        parsed = date(year + 1, month, day)
                    except ValueError:
                        return None
                return parsed

    m = re.search(r"\b(\d{1,2})\s*(?:-?го|числа)\b", text, re.IGNORECASE)
    if m:
        day = int(m.group(1))
        year, month = base.year, base.month
        try:
            parsed = date(year, month, day)
        except ValueError:
            return None
        if parsed < base:
            month += 1
            if month > 12:
                month, year = 1, year + 1
            try:
                parsed = date(year, month, day)
            except ValueError:
                return None
        return parsed
    return None


def _district_tokens(text: str) -> list[str]:
    cleaned = re.sub(
        r"\b(?:район|районе|районы|теперь|давайте|лучше|хочу|в|на)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^\w\s-]", " ", cleaned, flags=re.UNICODE)
    tokens = [t for t in cleaned.split() if len(t) > 2 and not t.isdigit()]
    return [" ".join(tokens)] if tokens else []


def _classify_slot(context: str, old: str, new: str) -> str:
    """Which slot does this correction target? Order = specificity."""
    blob = f"{context} {old} {new}"
    if _DISTRICT_HINT.search(blob) or not re.search(r"\d", f"{old} {new}"):
        if not re.search(r"\d", f"{old} {new}"):
            return "districts"
    if _BEDROOMS_HINT.search(blob):
        return "bedrooms"
    if _CHECK_OUT_HINT.search(context) and not _CHECK_IN_HINT.search(context):
        return "check_out"
    if _CHECK_IN_HINT.search(blob) or re.search(
        r"\d{1,2}\s*(?:-го|числа|[.\-/]\d)", f"{old} {new}"
    ):
        return "check_in"
    if _STAY_HINT.search(blob) and re.search(r"месяц|год", blob, re.IGNORECASE):
        return "stay_months"
    if _GUESTS_HINT.search(blob):
        return "guests"
    if _BUDGET_HINT.search(blob):
        return "budget"
    # Bare «не 150, а 200» in a rental dialogue is a budget correction.
    value = _parse_amount(new)
    if value is not None and value >= 1000:
        return "budget"
    if value is not None and value <= 20:
        return "guests"
    return ""


def _value_for_slot(slot: str, new_text: str, *, today: date | None = None) -> Any:
    if slot == "budget":
        return _parse_amount(new_text)
    if slot in ("guests", "bedrooms"):
        return _parse_int(new_text)
    if slot in ("check_in", "check_out"):
        return _parse_day_or_date(new_text, today=today)
    if slot == "stay_months":
        if re.search(r"год", new_text, re.IGNORECASE):
            return 12.0
        n = _parse_int(new_text)
        return float(n) if n is not None else None
    if slot == "districts":
        tokens = _district_tokens(new_text)
        return tokens or None
    return None


def detect_correction(
    message: str,
    lead: LeadProfile | None = None,
    *,
    today: date | None = None,
) -> CorrectionResult:
    """Deterministic «не X, а Y» detection. Empty result = not a correction."""
    text = (message or "").strip()
    if not text:
        return CorrectionResult()

    match = _CORRECTION_FRAME.search(text)
    old_text = new_text = ""
    if match:
        old_text = match.group("old").strip()
        new_text = match.group("new").strip()
    else:
        match = _INSTEAD_FRAME.search(text)
        if match:
            old_text = match.group("old").strip()
            new_text = match.group("new").strip()

    if not match:
        return CorrectionResult()

    slot = _classify_slot(text, old_text, new_text)
    if not slot:
        return CorrectionResult(is_explicit=True, raw_old=old_text, raw_new=new_text)

    value = _value_for_slot(slot, new_text, today=today)
    if value is None:
        return CorrectionResult(is_explicit=True, raw_old=old_text, raw_new=new_text)

    return CorrectionResult(
        corrected_slots=[slot],
        target_values={slot: value},
        is_explicit=True,
        raw_old=old_text,
        raw_new=new_text,
    )


def is_explicit_restatement(message: str) -> bool:
    """«я уже говорил…» — treat the value in this message as CONFIRMED."""
    return bool(_RESTATE_FRAME.search(message or ""))


def apply_correction(lead: LeadProfile, result: CorrectionResult) -> list[str]:
    """Write corrected values onto the lead. Only the corrected slots change."""
    applied: list[str] = []
    for slot, value in (result.target_values or {}).items():
        if slot not in _SLOT_KEYS or value is None:
            continue
        if slot == "districts":
            lead.districts = list(value)
        else:
            setattr(lead, slot, value)
        applied.append(slot)
    return applied


def filter_update_to_correction(update: dict, result: CorrectionResult) -> dict:
    """Strip extract keys a correction must not touch (policy §3: only that slot)."""
    if not result.corrected_slots:
        return dict(update or {})
    allowed: set[str] = set()
    for slot in result.corrected_slots:
        allowed.update(_SLOT_KEYS.get(slot, (slot,)))
    # Non-criteria keys (language, intent flags, identity) always pass through.
    protected = set()
    for keys in _SLOT_KEYS.values():
        protected.update(keys)
    return {
        k: v for k, v in (update or {}).items()
        if k in allowed or k not in protected
    }
