"""Inbound message intent classification for Agent 6/7.

Deterministic guardrails first; optional Gemini fallback for ambiguous cases.
"""
from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Literal

Intent = Literal[
    "CONTINUE_CURRENT_REQUEST",
    "NEW_PROPERTY_SEARCH",
    "CHANGE_CRITERIA",
    "GENERAL_QUESTION",
    "HUMAN_REQUIRED",
]

if TYPE_CHECKING:
    from .qualifier import Session

_NEW_SEARCH_RE = re.compile(
    r"(?:"
    r"новый\s+(?:дом|объект|вариант|поиск)|"
    r"другой\s+(?:дом|объект|вариант)|"
    r"новый\s+район|"
    r"другом\s+районе|"
    r"в\s+другом\s+районе|"
    r"подобрать\s+заново|искать\s+заново|давай\s+искать|"
    r"начать\s+(?:новый\s+)?поиск|"
    r"новые\s+варианты|"
    r"подбери\s+(?:что-нибудь\s+)?другое|"
    r"что-нибудь\s+другое|"
    r"этот\s+вариант\s+не\s+подходит|"
    r"не\s+подходит\s+этот|"
    r"хочу\s+новый|"
    r"по\s+новым\s+критериям|"
    r"новым\s+критериям|"
    r"изменить\s+критерии|"
    r"подобрать\s+новый|"
    r"искать\s+нов|"
    r"ищем\s+нов|"
    r"закрыть\s+стар\w+\s+запрос|"
    r"забудь(?:те)?\s+(?:про\s+)?стар"
    r")",
    re.IGNORECASE,
)

_CHANGE_CRITERIA_RE = re.compile(
    r"(?:"
    r"теперь\s+бюджет|"
    r"бюджет\s+(?:теперь|вместо|до|около)\s*\d|"
    r"(?:\d+)\s+спален\s+(?:вместо|а\s+не)|"
    r"вместо\s+\d+\s+спален|"
    r"другой\s+район|"
    r"район\s+(?:другой|изменил|меняю)|"
    r"переехать\s+в|"
    r"сменить\s+(?:район|бюджет|даты)|"
    r"изменить\s+(?:район|бюджет|даты|срок)"
    r")",
    re.IGNORECASE,
)

_CONTINUE_OWNER_RE = re.compile(
    r"(?:"
    r"ответ\s+(?:от\s+)?владельц|"
    r"есть\s+ответ|"
    r"что\s+там\s+(?:по|с)|"
    r"ну\s+что\s+там|"
    r"ну\s+что\s*\?|"
    r"жд[ёе]м\s+владельц|"
    r"владелец\s+(?:ответил|отвечал)|"
    r"по\s+старому\s+(?:дому|объекту|варианту)|"
    r"старый\s+(?:дом|объект|вариант)"
    r")",
    re.IGNORECASE,
)

_HUMAN_REQUIRED_RE = re.compile(
    r"(?:позовите\s+менеджер|нужен\s+менеджер|живой\s+человек|"
    r"с\s+человеком|оператор)",
    re.IGNORECASE,
)


def _has_new_search_keywords(message: str) -> bool:
    return bool(_NEW_SEARCH_RE.search(message or ""))


def _has_change_criteria_keywords(message: str) -> bool:
    return bool(_CHANGE_CRITERIA_RE.search(message or ""))


def _has_continue_owner_keywords(message: str) -> bool:
    return bool(_CONTINUE_OWNER_RE.search(message or ""))


def classify_intent_deterministic(
    message: str,
    session: Session,
    update: dict,
) -> Intent | None:
    text = message or ""

    if _HUMAN_REQUIRED_RE.search(text):
        return "HUMAN_REQUIRED"

    if _has_continue_owner_keywords(text):
        return "CONTINUE_CURRENT_REQUEST"

    if session.awaiting_owner:
        filled = sum(
            1 for k in ("budget", "districts", "bedrooms", "check_in", "check_out")
            if update.get(k)
        )
        if filled >= 2 and (
            _has_new_search_keywords(text)
            or _has_change_criteria_keywords(text)
            or re.search(r"нужен\s+дом", text, re.IGNORECASE)
        ):
            return "NEW_PROPERTY_SEARCH"

    if _has_new_search_keywords(text):
        return "NEW_PROPERTY_SEARCH"

    if update.get("wants_alternatives") and session.awaiting_owner:
        # «покажите другие» while waiting owner — new search branch.
        if _has_change_criteria_keywords(text) or re.search(
            r"друг|ещё|альтернатив|нов", text, re.IGNORECASE
        ):
            return "NEW_PROPERTY_SEARCH"

    if _has_change_criteria_keywords(text):
        return "CHANGE_CRITERIA"

    return None


_INTENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {
            "type": "STRING",
            "description": (
                "CONTINUE_CURRENT_REQUEST | NEW_PROPERTY_SEARCH | "
                "CHANGE_CRITERIA | GENERAL_QUESTION | HUMAN_REQUIRED"
            ),
        },
    },
    "required": ["intent"],
}

_INTENT_PROMPT = """Классифицируй намерение клиента в контексте диалога аренды жилья.

Контекст:
{context}

История:
{history}

Сообщение клиента:
{message}

Правила:
- NEW_PROPERTY_SEARCH: клиент явно хочет другой объект/дом/район или начать поиск заново.
- CHANGE_CRITERIA: меняет бюджет/спальни/район/даты без полного отказа от диалога.
- CONTINUE_CURRENT_REQUEST: спрашивает статус текущего объекта/ответа владельца.
- HUMAN_REQUIRED: просит живого менеджера.
- GENERAL_QUESTION: общий вопрос, не про смену поиска.

Если клиент говорит «новый дом» или «другой объект» — это NEW_PROPERTY_SEARCH, не CONTINUE.
Верни JSON с полем intent."""


def classify_intent_llm(
    message: str,
    session: Session,
    *,
    context: str = "",
    history: str = "",
) -> Intent | None:
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    from .brain import _call, _text

    payload = {
        "contents": [{"parts": [{"text": _INTENT_PROMPT.format(
            context=context or "(нет)",
            history=history or "(нет)",
            message=message,
        )}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": _INTENT_SCHEMA,
            "temperature": 0.0,
        },
    }
    try:
        data = json.loads(_text(_call(payload)))
        intent = str(data.get("intent") or "").upper().replace(" ", "_")
        allowed = {
            "CONTINUE_CURRENT_REQUEST",
            "NEW_PROPERTY_SEARCH",
            "CHANGE_CRITERIA",
            "GENERAL_QUESTION",
            "HUMAN_REQUIRED",
        }
        if intent in allowed:
            return intent  # type: ignore[return-value]
    except Exception:
        pass
    return None


def classify_intent(
    message: str,
    session: Session,
    update: dict,
    *,
    context: str = "",
    history: str = "",
    use_llm: bool = True,
) -> Intent:
    """Classify client message; deterministic rules override LLM."""
    det = classify_intent_deterministic(message, session, update)
    if det is not None:
        return det

    if use_llm and (
        session.awaiting_owner
        or session.human_handoff_active
        or session.handoff_to_human
        or session.pending_owner_requests
    ):
        llm = classify_intent_llm(message, session, context=context, history=history)
        if llm is not None:
            return llm

    return "GENERAL_QUESTION"
