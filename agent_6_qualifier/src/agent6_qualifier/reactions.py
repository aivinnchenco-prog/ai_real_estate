"""Object reaction memory and preference signals (Wave 3, policy §6–8, §10).

Reactions are stored per search context: a NEW_PROPERTY_SEARCH starts a fresh
reaction memory while client identity survives. A reaction never becomes a hard
constraint on its own — it feeds ranking (see ``matching``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qualifier import Session


class ReactionType(str, Enum):
    LIKE = "LIKE"
    DISLIKE = "DISLIKE"
    TOO_EXPENSIVE = "TOO_EXPENSIVE"
    BAD_LOCATION = "BAD_LOCATION"
    TOO_SMALL = "TOO_SMALL"
    TOO_LARGE = "TOO_LARGE"
    STYLE_DISLIKE = "STYLE_DISLIKE"
    STYLE_LIKE = "STYLE_LIKE"
    NO_POOL = "NO_POOL"
    OTHER = "OTHER"


# Reactions that remove the object from the next shortlist (policy §10).
REJECTING_REACTIONS = frozenset({
    ReactionType.DISLIKE,
    ReactionType.TOO_EXPENSIVE,
    ReactionType.BAD_LOCATION,
    ReactionType.TOO_SMALL,
    ReactionType.TOO_LARGE,
    ReactionType.STYLE_DISLIKE,
    ReactionType.NO_POOL,
})

POSITIVE_REACTIONS = frozenset({ReactionType.LIKE, ReactionType.STYLE_LIKE})


@dataclass
class PreferenceSignal:
    kind: str          # price | location | style | size | pool | district
    value: str
    polarity: str      # positive | negative
    weight: float = 1.0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "polarity": self.polarity,
            "weight": self.weight,
        }


@dataclass
class DetectedReaction:
    reaction_type: ReactionType
    reason: str = ""
    signals: list[PreferenceSignal] = field(default_factory=list)
    # District praised while the object itself was rejected.
    keeps_district: bool = False


# Ordered: the first pattern that matches wins, so specific beats generic.
_PATTERNS: tuple[tuple[re.Pattern[str], ReactionType, str], ...] = (
    (re.compile(r"слишком\s+дорог|дорогов|очень\s+дорог|\bне\s+по\s+карману|"
                r"\bдорого\b", re.IGNORECASE),
     ReactionType.TOO_EXPENSIVE, "price_high"),
    (re.compile(r"слишком\s+далеко|далеко\s+от|далековато|\bдалеко\b|"
                r"неудобн\w*\s+располож", re.IGNORECASE),
     ReactionType.BAD_LOCATION, "distance"),
    (re.compile(r"мал\w*\s+бассейн|бассейн\s+маленьк|нужен\s+больше\s+бассейн|"
                r"без\s+бассейн|нет\s+бассейн", re.IGNORECASE),
     ReactionType.NO_POOL, "pool"),
    (re.compile(r"слишком\s+мал|маловат|тесн\w+|мало\s+места", re.IGNORECASE),
     ReactionType.TOO_SMALL, "size_small"),
    (re.compile(r"слишком\s+больш|великоват|избыточ", re.IGNORECASE),
     ReactionType.TOO_LARGE, "size_large"),
    (re.compile(r"хочу\s+современн|современне|более\s+современн|"
                r"посвежее|новее|модерн", re.IGNORECASE),
     ReactionType.STYLE_LIKE, "modern_interior"),
    (re.compile(r"\bне\s+нравится\s+интерьер|интерьер\s+\bне\s+нрав|"
                r"старый\s+ремонт|устаревш\w*\s+интерьер|"
                r"некрасив", re.IGNORECASE),
     ReactionType.STYLE_DISLIKE, "interior"),
    (re.compile(r"этот\s+не\s+подходит|\bне\s+подходит\s+этот|\bне\s+нрав\w+|"
                r"покажи(?:те)?\s+друг|друг\w+\s+вариант|"
                r"\bне\s+то\b", re.IGNORECASE),
     ReactionType.DISLIKE, "generic_dislike"),
    (re.compile(r"этот\s+нравится|нравится\s+этот|этот\s+вариант\s+нормальн|"
                r"этот\s+подходит|хорош\w+\s+вариант|беру\s+этот|"
                r"похож\w*\s+на\s+этот", re.IGNORECASE),
     ReactionType.LIKE, "generic_like"),
)

_DISTRICT_PRAISE = re.compile(
    r"район\s+(?:хорош|нрав|отличн|подход)|"
    r"(?:этот|такой)\s+район\s+(?:нрав|хорош|подход)|"
    r"нравится\s+район",
    re.IGNORECASE,
)
_SIMILAR_REQUEST = re.compile(
    r"похож\w*\s+на\s+(?:этот|такой)|что-то\s+похож|такой\s+же", re.IGNORECASE
)


def _signals_for(reaction: ReactionType, reason: str) -> list[PreferenceSignal]:
    mapping: dict[ReactionType, PreferenceSignal] = {
        ReactionType.TOO_EXPENSIVE: PreferenceSignal("price", "price_sensitive", "negative", 1.5),
        ReactionType.BAD_LOCATION: PreferenceSignal("location", "distance", "negative", 1.5),
        ReactionType.NO_POOL: PreferenceSignal("pool", "pool_required", "positive", 1.0),
        ReactionType.TOO_SMALL: PreferenceSignal("size", "bigger", "positive", 1.0),
        ReactionType.TOO_LARGE: PreferenceSignal("size", "smaller", "positive", 1.0),
        ReactionType.STYLE_LIKE: PreferenceSignal("style", "modern_interior", "positive", 1.0),
        ReactionType.STYLE_DISLIKE: PreferenceSignal("style", "modern_interior", "positive", 1.0),
    }
    signal = mapping.get(reaction)
    return [signal] if signal else []


def detect_reaction(message: str) -> DetectedReaction | None:
    """Deterministic reaction classification from a client message."""
    text = (message or "").strip()
    if not text:
        return None

    keeps_district = bool(_DISTRICT_PRAISE.search(text))

    for pattern, reaction, reason in _PATTERNS:
        if not pattern.search(text):
            continue
        # «этот район нравится, но дом не подходит» is a dislike that keeps the area.
        if reaction == ReactionType.LIKE and keeps_district and re.search(
            r"не\s+подходит|не\s+нрав", text, re.IGNORECASE
        ):
            reaction, reason = ReactionType.DISLIKE, "generic_dislike"
        signals = _signals_for(reaction, reason)
        return DetectedReaction(
            reaction_type=reaction,
            reason=reason,
            signals=signals,
            keeps_district=keeps_district,
        )

    if keeps_district:
        return DetectedReaction(
            reaction_type=ReactionType.OTHER,
            reason="district_positive",
            keeps_district=True,
        )
    if _SIMILAR_REQUEST.search(text):
        return DetectedReaction(reaction_type=ReactionType.LIKE, reason="similar_request")
    return None


# ---------- session storage ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reaction_records(session: Session) -> list[dict]:
    items = getattr(session, "object_reactions", None)
    if not isinstance(items, list):
        items = []
        session.object_reactions = items
    return items


def _pref_list(session: Session, attr: str) -> list[dict]:
    items = getattr(session, attr, None)
    if not isinstance(items, list):
        items = []
        setattr(session, attr, items)
    return items


def positive_preferences(session: Session) -> list[dict]:
    return _pref_list(session, "positive_preferences")


def negative_preferences(session: Session) -> list[dict]:
    return _pref_list(session, "negative_preferences")


def _merge_signal(bucket: list[dict], signal: PreferenceSignal) -> None:
    for item in bucket:
        if item.get("kind") == signal.kind and item.get("value") == signal.value:
            item["weight"] = round(float(item.get("weight") or 0) + signal.weight, 2)
            return
    bucket.append(signal.to_dict())


def apply_preference_signals(session: Session, signals: list[PreferenceSignal]) -> None:
    for signal in signals:
        bucket = (
            positive_preferences(session)
            if signal.polarity == "positive"
            else negative_preferences(session)
        )
        _merge_signal(bucket, signal)


def _praised_district(session: Session, object_id: str) -> str:
    """Which area «этот район нравится» refers to."""
    chosen = session.chosen
    if chosen is not None and (not object_id or chosen.object_id == object_id):
        if chosen.district:
            return chosen.district
    districts = session.lead.districts or []
    return districts[0] if districts else ""


def record_reaction(
    session: Session,
    object_id: str,
    reaction: DetectedReaction,
    *,
    at: str | None = None,
    district: str = "",
) -> dict:
    """Store a reaction, update liked/rejected memory and preference signals."""
    record = {
        "object_id": object_id or "",
        "reaction_type": reaction.reaction_type.value,
        "reason": reaction.reason,
        "timestamp": at or _now_iso(),
    }
    reaction_records(session).append(record)

    if object_id:
        if reaction.reaction_type in REJECTING_REACTIONS:
            _add_unique(session, "rejected_object_ids", object_id)
            _discard(session, "liked_object_ids", object_id)
        elif reaction.reaction_type in POSITIVE_REACTIONS:
            _add_unique(session, "liked_object_ids", object_id)
            _discard(session, "rejected_object_ids", object_id)

    signals = list(reaction.signals)
    if reaction.keeps_district:
        area = district or _praised_district(session, object_id)
        if area:
            signals.append(PreferenceSignal("district", area, "positive", 1.5))

    apply_preference_signals(session, signals)
    return record


def _list_attr(session: Session, attr: str) -> list:
    items = getattr(session, attr, None)
    if not isinstance(items, list):
        items = []
        setattr(session, attr, items)
    return items


def _add_unique(session: Session, attr: str, value: str) -> None:
    items = _list_attr(session, attr)
    if value not in items:
        items.append(value)


def _discard(session: Session, attr: str, value: str) -> None:
    items = _list_attr(session, attr)
    if value in items:
        items.remove(value)


def mark_shown(session: Session, object_ids: list[str]) -> None:
    for oid in object_ids:
        if oid:
            _add_unique(session, "shown_object_ids", oid)


def rejected_ids(session: Session) -> list[str]:
    return list(_list_attr(session, "rejected_object_ids"))


def liked_ids(session: Session) -> list[str]:
    return list(_list_attr(session, "liked_object_ids"))


def reset_reaction_memory(session: Session) -> None:
    """New search context — reaction memory does not carry over (policy §19)."""
    session.object_reactions = []
    session.liked_object_ids = []
    session.rejected_object_ids = []
    session.shown_object_ids = []
    session.positive_preferences = []
    session.negative_preferences = []


def reaction_target_object(session: Session) -> str:
    """Which object a bare «слишком дорого» refers to."""
    if session.chosen is not None:
        return session.chosen.object_id
    if session.lead.preferred_object_id:
        return session.lead.preferred_object_id
    shown = _list_attr(session, "shown_object_ids")
    return shown[-1] if shown else ""


def acknowledge_reaction(reaction: DetectedReaction) -> str:
    """Short, non-defensive acknowledgement (policy §20 anti-patterns)."""
    kind = reaction.reaction_type
    if kind == ReactionType.TOO_EXPENSIVE:
        return "Понял, по цене не подходит — подберу вариант дешевле."
    if kind == ReactionType.BAD_LOCATION:
        return "Понял, расположение не подходит."
    if kind == ReactionType.NO_POOL:
        return "Понял, бассейн важен — учту в подборе."
    if kind == ReactionType.TOO_SMALL:
        return "Понял, нужен вариант просторнее."
    if kind == ReactionType.TOO_LARGE:
        return "Понял, нужен вариант компактнее."
    if kind == ReactionType.STYLE_LIKE:
        return "Понял, ориентируюсь на современный интерьер."
    if kind == ReactionType.STYLE_DISLIKE:
        return "Понял, интерьер не подошёл — поищу современнее."
    if kind == ReactionType.LIKE:
        return "Отлично, беру этот вариант за ориентир."
    if kind == ReactionType.DISLIKE:
        return "Понял, этот вариант убираю из подборки."
    return "Понял вас."
