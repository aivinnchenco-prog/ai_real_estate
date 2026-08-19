"""Offline Agent Coach / evaluator — NOT on the live response path."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CoachFinding:
    code: str
    severity: str  # info | warn | fail
    detail: str


@dataclass
class CoachReport:
    agent: str
    findings: list[CoachFinding] = field(default_factory=list)
    score: float = 1.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_agent6_turn(
    *,
    known_fields: list[str] | None = None,
    asked_fields: list[str] | None = None,
    questions_in_reply: int = 0,
    promised_unconfirmed: bool = False,
    ignored_client_facts: bool = False,
    conversation_progressed: bool = True,
    objection_handled: bool | None = None,
) -> CoachReport:
    """Offline rubric for Agent6 turns. Never called from webhook hot path by default."""
    findings: list[CoachFinding] = []
    known = set(known_fields or [])
    asked = set(asked_fields or [])
    overlap = known & asked
    if overlap:
        findings.append(
            CoachFinding(
                "repeat_known_question",
                "fail",
                f"повтор известных полей: {sorted(overlap)}",
            )
        )
    if questions_in_reply >= 5:
        findings.append(
            CoachFinding(
                "too_many_questions",
                "warn",
                f"слишком много вопросов в одном сообщении: {questions_in_reply}",
            )
        )
    if promised_unconfirmed:
        findings.append(
            CoachFinding(
                "promised_unconfirmed",
                "fail",
                "обещана неподтверждённая availability/цена",
            )
        )
    if ignored_client_facts:
        findings.append(
            CoachFinding(
                "ignored_context",
                "fail",
                "проигнорированы факты из сообщения клиента",
            )
        )
    if objection_handled is False:
        findings.append(
            CoachFinding("objection_missed", "warn", "возражение не обработано"),
        )
    if not conversation_progressed:
        findings.append(
            CoachFinding("no_progress", "warn", "диалог не продвинулся"),
        )

    score = 1.0
    for f in findings:
        if f.severity == "fail":
            score -= 0.25
        elif f.severity == "warn":
            score -= 0.1
    return CoachReport(agent="AGENT6", findings=findings, score=max(0.0, score))


def evaluate_agent7_turn(
    *,
    asked_needed_questions: bool = True,
    leaked_client_budget: bool = False,
    structured_verdict: bool = True,
    correlation_ok: bool = True,
    price_change_handled: bool | None = None,
) -> CoachReport:
    findings: list[CoachFinding] = []
    if not asked_needed_questions:
        findings.append(
            CoachFinding("missing_owner_questions", "warn", "не заданы нужные вопросы owner")
        )
    if leaked_client_budget:
        findings.append(
            CoachFinding("budget_leak", "fail", "клиентский budget передан владельцу")
        )
    if not structured_verdict:
        findings.append(
            CoachFinding("unstructured_verdict", "warn", "нет structured verdict")
        )
    if not correlation_ok:
        findings.append(
            CoachFinding("correlation_broken", "fail", "сломана корреляция owner→client")
        )
    if price_change_handled is False:
        findings.append(
            CoachFinding("price_change_missed", "warn", "смена цены не обработана")
        )
    score = 1.0
    for f in findings:
        if f.severity == "fail":
            score -= 0.3
        elif f.severity == "warn":
            score -= 0.1
    return CoachReport(agent="AGENT7", findings=findings, score=max(0.0, score))
