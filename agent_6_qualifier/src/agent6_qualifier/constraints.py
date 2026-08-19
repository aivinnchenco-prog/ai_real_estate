"""HARD vs SOFT constraint classification (Wave 3, policy §8).

«Только Банг Тао» is a filter; «лучше Банг Тао» is a ranking hint. An LLM may
propose a semantic class, but the merge that lands on the session is
deterministic and lives here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .qualifier import Session


class Strength(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"


# Limiting language → HARD.
_HARD_MARKERS = re.compile(
    r"\bтолько\b|\bисключительно\b|\bне\s+выше\b|\bне\s+больше\b|\bне\s+более\b|"
    r"\bне\s+меньше\b|\bне\s+менее\b|\bмаксимум\b|\bминимум\b|\bобязательно\b|"
    r"\bстрого\b|\bне\s+дороже\b|\bпотолок\b|\bлимит\b|\bтвёрдо\b|\bтвердо\b|"
    r"\bни\s+в\s+коем\s+случае\b|\bдругой\s+не\s+подойд|\bнужно\s+именно\b|"
    r"\bименно\b",
    re.IGNORECASE,
)

# Preference language → SOFT (checked first: «лучше не выше» is still a wish).
_SOFT_MARKERS = re.compile(
    r"\bлучше\b|\bжелательно\b|\bпредпочтительн\w*\b|\bхотелось\s+бы\b|"
    r"\bпо\s+возможности\b|\bв\s+идеале\b|\bориентир\w*\b|\bпримерно\b|"
    r"\bгде-то\b|\bоколо\b|\bскорее\b|\bесли\s+можно\b|\bнеплохо\s+бы\b",
    re.IGNORECASE,
)

# Slot keywords used to attribute a marker to the right slot.
_SLOT_KEYWORDS = {
    "budget": re.compile(r"бюджет|цена|стоимост|฿|thb|тысяч|тыс\b|дорог", re.IGNORECASE),
    "districts": re.compile(r"район|локац|area|рядом\s+с", re.IGNORECASE),
    "bedrooms": re.compile(r"спал|комнат|bedroom", re.IGNORECASE),
    "guests": re.compile(r"гост|человек|прожива", re.IGNORECASE),
    "pets": re.compile(r"животн|питомц|собак|кошк|кот\b", re.IGNORECASE),
    "check_in": re.compile(r"заезд|заселен", re.IGNORECASE),
    "stay_months": re.compile(r"срок|месяц|год\b|контракт", re.IGNORECASE),
}

# Slot defaults when the client gave a value with no qualifying language.
_DEFAULT_STRENGTH = {
    "budget": Strength.SOFT,
    "districts": Strength.SOFT,
    "bedrooms": Strength.SOFT,
    "guests": Strength.HARD,
    "pets": Strength.HARD,
    "check_in": Strength.HARD,
    "stay_months": Strength.SOFT,
}


@dataclass
class ConstraintDecision:
    slot: str
    strength: Strength
    value: Any = None
    phrase: str = ""

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "strength": self.strength.value,
            "value": _jsonable(self.value),
            "phrase": self.phrase[:120],
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return value


def _clause_for_slot(message: str, slot: str) -> str:
    """The comma/clause chunk that mentions this slot, so markers don't bleed."""
    keyword = _SLOT_KEYWORDS.get(slot)
    if keyword is None:
        return message
    chunks = re.split(r"[,;.!?\n]", message or "")
    hits = [c for c in chunks if keyword.search(c)]
    return " ".join(hits) if hits else ""


def classify_constraint(
    slot: str,
    message: str,
    *,
    value: Any = None,
) -> Strength:
    """HARD/SOFT for one slot given the client's phrasing."""
    clause = _clause_for_slot(message or "", slot)
    if clause:
        if _SOFT_MARKERS.search(clause):
            return Strength.SOFT
        if _HARD_MARKERS.search(clause):
            return Strength.HARD
    # No slot-specific clause: a whole-message marker still counts when the
    # message speaks about a single criterion.
    if not clause and message:
        if _SOFT_MARKERS.search(message):
            return Strength.SOFT
        if _HARD_MARKERS.search(message):
            return Strength.HARD
    return _DEFAULT_STRENGTH.get(slot, Strength.SOFT)


# ---------- session storage ----------

def _bucket(session: Session, attr: str) -> list[dict]:
    items = getattr(session, attr, None)
    if not isinstance(items, list):
        items = []
        setattr(session, attr, items)
    return items


def hard_constraints(session: Session) -> list[dict]:
    return _bucket(session, "hard_constraints")


def soft_preferences(session: Session) -> list[dict]:
    return _bucket(session, "soft_preferences")


def record_constraint(session: Session, decision: ConstraintDecision) -> None:
    """Deterministic merge: one entry per slot, latest classification wins."""
    target = (
        hard_constraints(session)
        if decision.strength == Strength.HARD
        else soft_preferences(session)
    )
    other = (
        soft_preferences(session)
        if decision.strength == Strength.HARD
        else hard_constraints(session)
    )
    other[:] = [i for i in other if i.get("slot") != decision.slot]
    for item in target:
        if item.get("slot") == decision.slot:
            item.update(decision.to_dict())
            return
    target.append(decision.to_dict())


def classify_and_record(
    session: Session,
    slots: list[str] | tuple[str, ...],
    message: str,
) -> list[ConstraintDecision]:
    """Classify each slot touched this turn and merge into the session."""
    decisions: list[ConstraintDecision] = []
    for slot in slots:
        if slot not in _DEFAULT_STRENGTH:
            continue
        value = getattr(session.lead, slot, None)
        strength = classify_constraint(slot, message, value=value)
        decision = ConstraintDecision(
            slot=slot, strength=strength, value=value, phrase=message or ""
        )
        record_constraint(session, decision)
        decisions.append(decision)
    return decisions


def strength_for(session: Session, slot: str) -> Strength | None:
    for item in hard_constraints(session):
        if item.get("slot") == slot:
            return Strength.HARD
    for item in soft_preferences(session):
        if item.get("slot") == slot:
            return Strength.SOFT
    return None


def is_hard(session: Session, slot: str) -> bool:
    return strength_for(session, slot) == Strength.HARD


def reset_constraints(session: Session) -> None:
    session.hard_constraints = []
    session.soft_preferences = []


def summary_lines(session: Session) -> tuple[list[str], list[str]]:
    """Human-readable lists for the handoff note."""
    def _fmt(item: dict) -> str:
        value = item.get("value")
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"{item.get('slot')}={value}"

    return (
        [_fmt(i) for i in hard_constraints(session)],
        [_fmt(i) for i in soft_preferences(session)],
    )
