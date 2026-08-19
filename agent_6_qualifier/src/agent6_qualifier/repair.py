"""Conversation repair state (Wave 3, policy §12–14).

Repair is not an apology loop: it restates the new understanding in one line
and asks at most one question. Handoff happens only after
``AGENT6_MAX_REPAIR_ATTEMPTS`` failed repairs — never on a first
misunderstanding, so managers do not get spammed.
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qualifier import Session

DEFAULT_MAX_REPAIR_ATTEMPTS = 2


class RepairReason(str, Enum):
    EXPLICIT_MISUNDERSTANDING = "explicit_misunderstanding"
    REPEATED_CLIENT_REQUEST = "repeated_client_request"
    REPEATED_BOT_REPLY = "repeated_bot_reply"
    LOW_CONFIDENCE_EXTRACT = "low_confidence_extract"
    UNRESOLVED_CONTRADICTION = "unresolved_contradiction"


# «ты понимаешь?», «я уже сказал», «я не это имею в виду», «нет, не так»
_EXPLICIT_MISUNDERSTANDING = re.compile(
    r"ты\s+(?:меня\s+)?понима|вы\s+(?:меня\s+)?понима|понимаешь\s+(?:меня|вообще)|"
    r"я\s+уже\s+(?:сказал|говорил|писал|это\s+говорил)|"
    r"не\s+(?:это|то)\s+име(?:л|ла|ю|ем)|"
    r"нет,?\s+не\s+так|не\s+так\s+понял|ты\s+не\s+понял|вы\s+не\s+поняли|"
    r"опять\s+не\s+то|уже\s+(?:третий|второй|\d+)\s+раз|"
    r"читай(?:те)?\s+внимательн",
    re.IGNORECASE,
)
_FRUSTRATION = re.compile(
    r"бесит|достал|надоел|раздража|это\s+бессмысленн|вы\s+издевает",
    re.IGNORECASE,
)
# A message with no parseable content at all.
_EMPTY_SIGNAL = re.compile(r"^[\s\W_]{1,12}$", re.UNICODE)

_SIMILARITY_THRESHOLD = 0.86


def max_repair_attempts() -> int:
    try:
        return max(1, int(os.getenv("AGENT6_MAX_REPAIR_ATTEMPTS", "")
                          or DEFAULT_MAX_REPAIR_ATTEMPTS))
    except ValueError:
        return DEFAULT_MAX_REPAIR_ATTEMPTS


@dataclass
class RepairAssessment:
    triggered: bool = False
    reason: RepairReason | None = None
    should_handoff: bool = False
    misunderstanding_count: int = 0

    def __bool__(self) -> bool:
        return self.triggered


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _last_client_messages(session: Session, limit: int = 4) -> list[str]:
    return [
        m.get("text", "")
        for m in (session.history or [])
        if m.get("role") == "user"
    ][-limit:]


def _last_bot_messages(session: Session, limit: int = 3) -> list[str]:
    return [
        m.get("text", "")
        for m in (session.history or [])
        if m.get("role") == "assistant"
    ][-limit:]


def client_repeats_request(session: Session, message: str) -> bool:
    """Client restated the same request in different words."""
    for previous in _last_client_messages(session):
        if _similar(previous, message) >= _SIMILARITY_THRESHOLD:
            return True
    return False


def bot_repeated_itself(session: Session) -> bool:
    replies = _last_bot_messages(session)
    if len(replies) < 2:
        return False
    return _similar(replies[-1], replies[-2]) >= _SIMILARITY_THRESHOLD


def detect_repair_signal(
    session: Session,
    message: str,
    update: dict,
    *,
    has_unresolved_contradiction: bool = False,
) -> RepairReason | None:
    """Deterministic repair trigger, or None when the dialogue is healthy."""
    text = message or ""

    if _EXPLICIT_MISUNDERSTANDING.search(text) or _FRUSTRATION.search(text):
        return RepairReason.EXPLICIT_MISUNDERSTANDING

    if client_repeats_request(session, text):
        return RepairReason.REPEATED_CLIENT_REQUEST

    if bot_repeated_itself(session):
        return RepairReason.REPEATED_BOT_REPLY

    if has_unresolved_contradiction and int(
        getattr(session, "misunderstanding_count", 0) or 0
    ) >= 1:
        return RepairReason.UNRESOLVED_CONTRADICTION

    if _EMPTY_SIGNAL.match(text) and not (update or {}):
        return RepairReason.LOW_CONFIDENCE_EXTRACT

    return None


def register_repair(session: Session, reason: RepairReason) -> RepairAssessment:
    """Increment the counter and decide whether the threshold is exceeded."""
    count = int(getattr(session, "misunderstanding_count", 0) or 0) + 1
    session.misunderstanding_count = count
    session.repair_mode = True
    session.last_repair_reason = reason.value

    should_handoff = count > max_repair_attempts()
    return RepairAssessment(
        triggered=True,
        reason=reason,
        should_handoff=should_handoff,
        misunderstanding_count=count,
    )


def assess(
    session: Session,
    message: str,
    update: dict,
    *,
    has_unresolved_contradiction: bool = False,
) -> RepairAssessment:
    reason = detect_repair_signal(
        session,
        message,
        update,
        has_unresolved_contradiction=has_unresolved_contradiction,
    )
    if reason is None:
        return RepairAssessment(
            misunderstanding_count=int(getattr(session, "misunderstanding_count", 0) or 0)
        )
    return register_repair(session, reason)


def clear_repair(session: Session) -> None:
    """Client moved on — leave repair mode without wiping the audit counter."""
    session.repair_mode = False


def reset_repair(session: Session) -> None:
    session.misunderstanding_count = 0
    session.repair_mode = False
    session.last_repair_reason = ""


def build_repair_reply(
    session: Session,
    understanding: str,
    question: str = "",
) -> str:
    """Short recap of the new understanding plus at most one question."""
    parts: list[str] = []
    recap = (understanding or "").strip()
    parts.append(recap or "Понял, давайте сверимся.")
    if question:
        parts.append(question.strip())
    return "\n\n".join(p for p in parts if p)
