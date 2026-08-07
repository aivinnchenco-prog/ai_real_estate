"""Policy engine: deterministic decisions after Gemini interpretation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config_loader import load_intro_templates
from .gemini import GeminiInterpretation, confidence_action
from .role_classifier import RoleResult, classify_role_deterministic, notion_role_value
from .state_machine import BusinessState
from .whatsapp import WhatsAppParseResult, extract_whatsapp_from_text


@dataclass
class PolicyDecision:
    new_state: BusinessState | None = None
    outreach_status: str | None = None
    send_message: str | None = None
    response_intent: str | None = None
    save_whatsapp: str | None = None
    save_role: str | None = None
    manual_review: bool = False
    decline: bool = False
    complete: bool = False
    notes: list[str] = field(default_factory=list)
    whatsapp_candidate: str | None = None


PROPERTY_TYPE_MAP = {
    "вилла": "villa", "villa": "villa",
    "квартира": "apartment", "apartment": "apartment", "апартаменты": "apartment",
    "кондо": "apartment", "condo": "apartment",
    "дом": "house", "house": "house",
    "таунхаус": "house", "townhouse": "house",
}


def intro_template(property_type: str, language: str = "ru") -> str:
    templates = load_intro_templates()
    lang = templates.get(language) or templates.get("ru") or {}
    key = PROPERTY_TYPE_MAP.get((property_type or "").strip().lower(), "default")
    return lang.get(key) or lang.get("default") or ""


def role_question_template(language: str = "ru") -> str:
    templates = load_intro_templates().get("role_question") or {}
    return templates.get(language) or templates.get("ru") or (
        "Спасибо! Подскажите, пожалуйста, вы собственник этого объекта или представляете его как агент?"
    )


def policy_templates() -> dict[str, dict[str, str]]:
    return {
        "ACK_WAIT": {
            "ru": "Конечно, спасибо. Буду ждать.",
            "en": "Of course, thank you. I will wait.",
        },
        "ANSWER_UNKNOWN_FACT": {
            "ru": "Точные детали я сейчас уточняю, чтобы не дать вам неверную информацию. Подскажите пока, пожалуйста, ваш WhatsApp для связи.",
            "en": "I am confirming the exact details so I do not give you incorrect information. Could you please share your WhatsApp number?",
        },
        "ANSWER_KNOWN_FACT": {
            "ru": "{fact}",
            "en": "{fact}",
        },
        "ASK_WHATSAPP": {
            "ru": "Подскажите, пожалуйста, ваш WhatsApp для связи.",
            "en": "Could you please share your WhatsApp number?",
        },
        "ASK_ROLE": {
            "ru": role_question_template("ru"),
            "en": role_question_template("en"),
        },
        "DECLINE_ACK": {
            "ru": "Понял, спасибо за ответ. Больше не беспокою.",
            "en": "Understood, thank you. I will not contact you again.",
        },
        "WHO_ARE_YOU": {
            "ru": "Я представляю агентство Open Home. У нас есть потенциальный клиент на этот объект.",
            "en": "I represent Open Home agency. We have a potential client for this property.",
        },
        "WHY_WHATSAPP": {
            "ru": "WhatsApp удобен для быстрого согласования деталей по объекту.",
            "en": "WhatsApp is convenient for quickly coordinating property details.",
        },
    }


def format_known_fact(question_type: str | None, known_facts: dict) -> str | None:
    mapping = {
        "dates": "dates",
        "pets": "pets_allowed",
        "nationality": "nationality",
        "guests": "guests",
        "budget": "budget",
        "duration": "duration",
        "price": "price",
    }
    key = mapping.get(question_type or "")
    if not key:
        return None
    value = known_facts.get(key)
    if value in (None, "", []):
        return None
    return str(value)


DECLINE_KEYWORDS = (
    "don't contact", "do not contact", "stop", "not interested", "no whatsapp",
    "не интересно", "не работаю с агентами", "не давайте", "не пишите",
)


def _is_decline_message(text: str) -> bool:
    lowered = (text or "").lower()
    return any(k in lowered for k in DECLINE_KEYWORDS)


def apply_inbound_policy(
    *,
    state: BusinessState,
    message: str,
    interpretation: GeminiInterpretation | None,
    known_facts: dict,
    gemini_available: bool,
) -> PolicyDecision:
    decision = PolicyDecision()
    lang = (interpretation.language if interpretation else "en") or "en"
    templates = policy_templates()

    if _is_decline_message(message) or (interpretation and interpretation.decline) or (interpretation and interpretation.intent == "decline"):
        decision.decline = True
        decision.new_state = BusinessState.DECLINED
        decision.outreach_status = "declined"
        decision.send_message = templates["DECLINE_ACK"].get(lang) or templates["DECLINE_ACK"]["en"]
        decision.response_intent = "DECLINE_ACK"
        return decision

    if interpretation and interpretation.intent == "wait":
        decision.send_message = templates["ACK_WAIT"].get(lang) or templates["ACK_WAIT"]["en"]
        decision.response_intent = "ACK_WAIT"
        return decision

    if state == BusinessState.WAITING_CONTACT:
        wa = extract_whatsapp_from_text(message)
        if not wa.valid and interpretation and interpretation.intent == "whatsapp_provided":
            wa = extract_whatsapp_from_text(interpretation.whatsapp or message)
        if wa.valid:
            decision.save_whatsapp = wa.normalized
            decision.whatsapp_candidate = wa.raw
            decision.new_state = BusinessState.CONTACT_RECEIVED
            decision.outreach_status = "waiting_role"
            decision.send_message = role_question_template(lang)
            decision.response_intent = "ASK_ROLE"
            return decision

        if interpretation and interpretation.intent == "question":
            return _handle_question(decision, interpretation, known_facts, lang, templates, ask_whatsapp=True)

        if interpretation and interpretation.intent == "greeting":
            decision.send_message = templates["ASK_WHATSAPP"].get(lang)
            decision.response_intent = "ASK_WHATSAPP"
            return decision

        if interpretation is None and not gemini_available:
            decision.manual_review = True
            decision.new_state = BusinessState.MANUAL_REVIEW
            decision.outreach_status = "manual_review"
            return decision

        if interpretation and confidence_action(interpretation.confidence) == "manual_review":
            decision.manual_review = True
            decision.new_state = BusinessState.MANUAL_REVIEW
            decision.outreach_status = "manual_review"
            return decision

        return decision

    if state == BusinessState.WAITING_ROLE:
        role = classify_role_deterministic(message)
        if role.role == "unknown" and interpretation:
            if interpretation.intent == "role_answer" and interpretation.role in ("owner", "agent"):
                if confidence_action(interpretation.confidence) != "manual_review":
                    role = RoleResult(interpretation.role, interpretation.confidence, "gemini")
        if role.role in ("owner", "agent"):
            decision.save_role = notion_role_value(role.role)
            decision.new_state = BusinessState.COMPLETE
            decision.outreach_status = "complete"
            decision.complete = True
            return decision
        decision.manual_review = True
        decision.new_state = BusinessState.MANUAL_REVIEW
        decision.outreach_status = "manual_review"
        return decision

    return decision


def _handle_question(
    decision: PolicyDecision,
    interpretation: GeminiInterpretation,
    known_facts: dict,
    lang: str,
    templates: dict,
    *,
    ask_whatsapp: bool,
) -> PolicyDecision:
    qtype = interpretation.question_type or "other"
    if qtype == "who_are_you":
        decision.send_message = templates["WHO_ARE_YOU"].get(lang)
        decision.response_intent = "ANSWER_KNOWN_FACT"
    elif qtype == "why_whatsapp":
        decision.send_message = templates["WHY_WHATSAPP"].get(lang)
        decision.response_intent = "ANSWER_KNOWN_FACT"
    else:
        fact = format_known_fact(qtype, known_facts)
        if fact and not _would_hallucinate(qtype, known_facts):
            tmpl = templates["ANSWER_KNOWN_FACT"].get(lang) or "{fact}"
            decision.send_message = tmpl.format(fact=fact)
            decision.response_intent = "ANSWER_KNOWN_FACT"
        else:
            decision.send_message = templates["ANSWER_UNKNOWN_FACT"].get(lang)
            decision.response_intent = "ANSWER_UNKNOWN_FACT"
    if ask_whatsapp and decision.send_message and "WhatsApp" not in decision.send_message and lang == "en":
        decision.send_message += " Could you please share your WhatsApp number?"
    return decision


def _would_hallucinate(question_type: str, known_facts: dict) -> bool:
    required = {
        "dates": "dates", "pets": "pets_allowed", "nationality": "nationality",
        "guests": "guests", "budget": "budget", "duration": "duration",
    }
    key = required.get(question_type or "")
    if not key:
        return False
    return known_facts.get(key) in (None, "", [])


def reject_unsupported_generated_fact(response: str, known_facts: dict) -> bool:
    """Return True if response appears to invent unsupported facts."""
    forbidden_patterns = [
        (r"\d{1,2}[./]\d{1,2}", "dates"),
        (r"\b\d+\s*(guest|гост)", "guests"),
        (r"\bTHB\b|\b฿\b|\$\d+", "budget"),
        (r"citizenship|nationality|граждан", "nationality"),
    ]
    for pattern, key in forbidden_patterns:
        if known_facts.get(key) in (None, "", []) and __import__("re").search(pattern, response, __import__("re").I):
            return True
    return False
