"""Contradiction detection and narrow clarification (Wave 3, policy §4–5).

A changed value is **not** a contradiction. A contradiction is only raised when
two values are both plausible and active and nothing in the message says which
one replaces the other — then the agent asks exactly one narrow question.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Any

from .morphology import months_phrase

if TYPE_CHECKING:
    from .qualifier import Session


class ContradictionType(str, Enum):
    DATE_CONFLICT = "DATE_CONFLICT"
    STAY_CONFLICT = "STAY_CONFLICT"
    BUDGET_CONFLICT = "BUDGET_CONFLICT"
    GUESTS_CONFLICT = "GUESTS_CONFLICT"
    DISTRICT_CONFLICT = "DISTRICT_CONFLICT"
    OBJECT_CONFLICT = "OBJECT_CONFLICT"
    BOOKING_INTENT_CONFLICT = "BOOKING_INTENT_CONFLICT"


@dataclass
class Contradiction:
    type: ContradictionType
    slot: str
    old_value: Any = None
    new_value: Any = None
    question: str = ""
    resolved: bool = False
    detail: str = ""
    # Only genuinely ambiguous conflicts stop the flow with a question;
    # a newer explicit scalar simply replaces the old value (policy §4).
    needs_clarification: bool = False

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "slot": self.slot,
            "old_value": _jsonable(self.old_value),
            "new_value": _jsonable(self.new_value),
            "question": self.question,
            "resolved": self.resolved,
            "detail": self.detail,
            "needs_clarification": self.needs_clarification,
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


# The client already said which value wins — never a contradiction.
_REPLACEMENT_MARKER = re.compile(
    r"\bвместо\b|\bтеперь\b|\bпоменял\w*\b|\bизменил\w*\b|\bперенес\w*\b|"
    r"\bсдвига\w*\b|\bпередума\w*\b|\bуже\s+не\b|\bбольше\s+не\b|"
    r"\bне\s+\S+\s*,?\s+а\b",
    re.IGNORECASE,
)

_YEAR_STAY = re.compile(r"\bна\s+год\b|\bгодов\w*\s+контракт|\bконтракт\s+на\s+год", re.IGNORECASE)
_BUDGET_DROPPED = re.compile(r"бюджета\s+нет|без\s+бюджета|бюджет\s+не\s+важен", re.IGNORECASE)
_WANTS_OTHER_OBJECT = re.compile(
    r"хочу\s+друг\w+\s+объект|друг\w+\s+(?:дом|объект|вариант)|"
    r"этот\s+не\s+подходит|покажи(?:те)?\s+друг",
    re.IGNORECASE,
)
_BOOKING_DECLINE = re.compile(
    r"не\s+готов\w*\s+брон|передума\w*\s+брон|отмен\w*\s+брон|"
    r"пока\s+не\s+бронир", re.IGNORECASE
)

# Under this many months apart the stay values are considered compatible.
_STAY_TOLERANCE_MONTHS = 1.0


def has_replacement_marker(message: str) -> bool:
    """True when the client made clear the new value replaces the old one."""
    return bool(_REPLACEMENT_MARKER.search(message or ""))


def _months_between(start: date, end: date) -> float:
    return round((end - start).days / 30.44, 2)


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _stay_conflict(
    session: Session, update: dict, message: str
) -> Contradiction | None:
    """Two incompatible stay durations with nothing saying which one wins.

    Both directions matter: «на год» on file then «до декабря» now, and an
    existing short window then «на год» now.
    """
    lead = session.lead
    if not lead.check_in:
        return None

    # Direction A: known stay_months vs a new explicit check-out.
    new_check_out = _coerce_date(update.get("check_out"))
    if lead.stay_months is not None and new_check_out is not None:
        implied = _months_between(lead.check_in, new_check_out)
        if implied > 0 and abs(implied - float(lead.stay_months)) > _STAY_TOLERANCE_MONTHS:
            return Contradiction(
                type=ContradictionType.STAY_CONFLICT,
                slot="stay_months",
                old_value=float(lead.stay_months),
                new_value=implied,
                detail=f"check_out={new_check_out.isoformat()}",
                needs_clarification=True,
            )

    # Direction B: known date window vs a new explicit stay duration.
    new_stay = update.get("stay_months")
    if new_stay is not None and lead.check_out is not None:
        try:
            new_value = float(new_stay)
        except (TypeError, ValueError):
            return None
        implied = _months_between(lead.check_in, lead.check_out)
        if implied > 0 and abs(implied - new_value) > _STAY_TOLERANCE_MONTHS:
            return Contradiction(
                type=ContradictionType.STAY_CONFLICT,
                slot="stay_months",
                old_value=implied,
                new_value=new_value,
                detail=f"check_out={lead.check_out.isoformat()}",
                needs_clarification=True,
            )
    return None


def _budget_conflict(session: Session, update: dict, message: str) -> Contradiction | None:
    lead = session.lead
    if lead.budget is None:
        return None
    if _BUDGET_DROPPED.search(message or ""):
        return Contradiction(
            type=ContradictionType.BUDGET_CONFLICT,
            slot="budget",
            old_value=lead.budget,
            new_value=None,
            detail="budget_dropped",
            needs_clarification=True,
        )
    new_budget = update.get("budget")
    if new_budget is None:
        return None
    try:
        new_value = float(new_budget)
    except (TypeError, ValueError):
        return None
    if abs(new_value - float(lead.budget)) < 1:
        return None
    return Contradiction(
        type=ContradictionType.BUDGET_CONFLICT,
        slot="budget",
        old_value=float(lead.budget),
        new_value=new_value,
    )


def _date_conflict(session: Session, update: dict, message: str) -> Contradiction | None:
    lead = session.lead
    new_check_in = update.get("check_in")
    if not lead.check_in or not new_check_in:
        return None
    try:
        parsed = (
            new_check_in
            if isinstance(new_check_in, date)
            else date.fromisoformat(str(new_check_in)[:10])
        )
    except ValueError:
        return None
    if parsed == lead.check_in:
        return None
    return Contradiction(
        type=ContradictionType.DATE_CONFLICT,
        slot="check_in",
        old_value=lead.check_in,
        new_value=parsed,
    )


def _guests_conflict(session: Session, update: dict, message: str) -> Contradiction | None:
    lead = session.lead
    new_guests = update.get("guests")
    if lead.guests is None or new_guests is None:
        return None
    try:
        value = int(new_guests)
    except (TypeError, ValueError):
        return None
    if value == lead.guests:
        return None
    return Contradiction(
        type=ContradictionType.GUESTS_CONFLICT,
        slot="guests",
        old_value=lead.guests,
        new_value=value,
    )


def _district_conflict(session: Session, update: dict, message: str) -> Contradiction | None:
    lead = session.lead
    new_districts = update.get("districts")
    if not lead.districts or not new_districts:
        return None
    known = {d.strip().lower() for d in lead.districts if d}
    incoming = {str(d).strip().lower() for d in new_districts if d}
    if not incoming or incoming & known:
        return None
    return Contradiction(
        type=ContradictionType.DISTRICT_CONFLICT,
        slot="districts",
        old_value=list(lead.districts),
        new_value=[str(d) for d in new_districts],
    )


def _object_conflict(session: Session, update: dict, message: str) -> Contradiction | None:
    if not session.only_chosen:
        return None
    if not _WANTS_OTHER_OBJECT.search(message or ""):
        return None
    return Contradiction(
        type=ContradictionType.OBJECT_CONFLICT,
        slot="preferred_object_id",
        old_value=session.lead.preferred_object_id,
        new_value=None,
        detail="only_chosen_vs_new_object",
    )


def _booking_conflict(session: Session, update: dict, message: str) -> Contradiction | None:
    if not session.booking_intent or session.booking_confirmed:
        return None
    if not _BOOKING_DECLINE.search(message or ""):
        return None
    return Contradiction(
        type=ContradictionType.BOOKING_INTENT_CONFLICT,
        slot="booking_intent",
        old_value=True,
        new_value=False,
    )


_DETECTORS = (
    _stay_conflict,
    _date_conflict,
    _budget_conflict,
    _guests_conflict,
    _district_conflict,
    _object_conflict,
    _booking_conflict,
)

# Conflicts that resolve themselves — the new value simply wins.
_SELF_RESOLVING = frozenset({
    ContradictionType.OBJECT_CONFLICT,
    ContradictionType.BOOKING_INTENT_CONFLICT,
})


def detect_contradictions(
    session: Session,
    update: dict,
    message: str,
    *,
    corrected_slots: list[str] | tuple[str, ...] = (),
) -> list[Contradiction]:
    """Return unattributed conflicts only.

    Explicitly corrected slots and messages carrying a replacement marker are
    treated as corrections (policy §4) and never produce a clarification.
    """
    corrected = set(corrected_slots or ())
    explicit_replacement = has_replacement_marker(message)

    found: list[Contradiction] = []
    for detector in _DETECTORS:
        item = detector(session, update or {}, message or "")
        if item is None:
            continue
        if item.slot in corrected:
            continue
        if item.type in _SELF_RESOLVING:
            item.resolved = True
            found.append(item)
            continue
        if not item.needs_clarification:
            # Recorded for the snapshot / handoff note, but the newer explicit
            # value simply wins — no question for the client.
            item.resolved = True
            found.append(item)
            continue
        if explicit_replacement:
            item.resolved = True
            found.append(item)
            continue
        item.question = build_clarification(item)
        found.append(item)
    return found


def _stay_phrase(months: Any, detail: str = "") -> str:
    """«на год» / «на 3 месяца» / «до 01.09.2026» for a clarification question."""
    try:
        value = float(months)
    except (TypeError, ValueError):
        return "прежний срок"
    if value >= 11:
        return "на год"
    if detail.startswith("check_out="):
        parsed = _coerce_date(detail.split("=", 1)[1])
        if parsed is not None and value < 11:
            return f"до {parsed.strftime('%d.%m.%Y')}"
    return f"на {months_phrase(round(value))}"


def _fmt_value(value: Any) -> str:
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def build_clarification(item: Contradiction) -> str:
    """One narrow question — never a repeat of the whole questionnaire."""
    if item.type == ContradictionType.STAY_CONFLICT:
        old_text = _stay_phrase(item.old_value, item.detail)
        new_text = _stay_phrase(item.new_value, item.detail)
        return (
            f"Ранее речь шла об аренде {old_text}, а сейчас — {new_text}. "
            f"Правильно понимаю, что срок изменился и ориентируемся {new_text}?"
        )
    if item.type == ContradictionType.DATE_CONFLICT:
        return (
            f"Уточню по датам: раньше был заезд {_fmt_value(item.old_value)}, "
            f"сейчас — {_fmt_value(item.new_value)}. "
            f"Ориентируемся на {_fmt_value(item.new_value)}?"
        )
    if item.type == ContradictionType.BUDGET_CONFLICT:
        if item.new_value is None:
            # Deliberately without the old figure: the client just said it is
            # no longer relevant, repeating it reads like arguing.
            return (
                "Ранее бюджет был ограничен. Правильно понимаю, что теперь "
                "верхнюю планку не ставим и смотрим шире?"
            )
        return (
            f"Уточню по бюджету: было {_fmt_value(item.old_value)} ฿, "
            f"стало {_fmt_value(item.new_value)} ฿. Ориентируемся на "
            f"{_fmt_value(item.new_value)} ฿?"
        )
    if item.type == ContradictionType.GUESTS_CONFLICT:
        return (
            f"Уточню по количеству гостей: было {_fmt_value(item.old_value)}, "
            f"сейчас — {_fmt_value(item.new_value)}. Верно?"
        )
    if item.type == ContradictionType.DISTRICT_CONFLICT:
        return (
            f"Раньше искали в районе {_fmt_value(item.old_value)}, "
            f"сейчас — {_fmt_value(item.new_value)}. "
            "Смотрим только новый район или оба?"
        )
    return "Уточните, пожалуйста, какой вариант актуален?"


def apply_self_resolving(session: Session, items: list[Contradiction]) -> list[str]:
    """Conflicts where the newest client intent simply wins (policy §J)."""
    applied: list[str] = []
    for item in items:
        if item.type == ContradictionType.OBJECT_CONFLICT:
            session.only_chosen = False
            applied.append(item.type.value)
        elif item.type == ContradictionType.BOOKING_INTENT_CONFLICT:
            session.booking_intent = False
            applied.append(item.type.value)
    return applied


# ---------- session-level storage ----------

def pending_contradictions(session: Session) -> list[dict]:
    items = getattr(session, "contradictions", None)
    if not isinstance(items, list):
        items = []
        session.contradictions = items
    return items


def record_contradictions(session: Session, items: list[Contradiction]) -> None:
    store = pending_contradictions(session)
    existing = {(i.get("type"), i.get("slot")) for i in store if not i.get("resolved")}
    for item in items:
        key = (item.type.value, item.slot)
        if key in existing:
            continue
        store.append(item.to_dict())
        existing.add(key)


def first_unresolved(session: Session) -> dict | None:
    for item in pending_contradictions(session):
        if not item.get("resolved"):
            return item
    return None


def has_unresolved(session: Session) -> bool:
    return first_unresolved(session) is not None


def resolve_contradiction(session: Session, slot: str = "", *, all_slots: bool = False) -> int:
    """Mark contradictions resolved once the client answered."""
    count = 0
    for item in pending_contradictions(session):
        if item.get("resolved"):
            continue
        if all_slots or not slot or item.get("slot") == slot:
            item["resolved"] = True
            count += 1
    return count


_AFFIRM = re.compile(
    r"\b(да|верно|правильно|точно|именно|ага|угу|конечно|yes|right|correct)\b",
    re.IGNORECASE,
)
_NEGATE = re.compile(r"\b(нет|не\s+так|неверно|no)\b", re.IGNORECASE)


def answers_clarification(message: str) -> bool | None:
    """True = confirmed, False = rejected, None = not an answer."""
    text = message or ""
    if _NEGATE.search(text):
        return False
    if _AFFIRM.search(text):
        return True
    return None
