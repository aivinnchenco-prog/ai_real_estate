"""Gemini + policy tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent9_connector.gemini import (
    ALLOWED_INTENTS,
    GeminiClient,
    GeminiInterpretation,
    confidence_action,
    parse_structured_json,
    sanitize_interpretation,
)
from agent9_connector.policy import (
    apply_inbound_policy,
    intro_template,
    reject_unsupported_generated_fact,
)
from agent9_connector.state_machine import BusinessState


class TestGemini:
    def test_json_success(self):
        data = parse_structured_json('{"intent":"wait","confidence":0.95}')
        assert data["intent"] == "wait"

    def test_invalid_json_retry_path(self):
        assert parse_structured_json("not json") is None

    def test_invalid_twice_manual(self):
        g = GeminiClient(api_key="")
        with pytest.raises(RuntimeError):
            g.interpret_inbound("hi", {})

    def test_invalid_json_retry_once(self, monkeypatch):
        calls = {"n": 0}

        class G(GeminiClient):
            def _call(self, prompt):
                calls["n"] += 1
                return "bad" if calls["n"] == 1 else '{"intent":"wait","confidence":0.95}'

        i = G(api_key="x").interpret_inbound("wait", {})
        assert i.intent == "wait"
        assert calls["n"] == 2

    def test_guests_question(self):
        interp = GeminiInterpretation(intent="question", question_type="guests", confidence=0.91)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="guests?", interpretation=interp, known_facts={}, gemini_available=True)
        assert d.send_message

    def test_wait_intent_policy(self):
        interp = GeminiInterpretation(intent="wait", confidence=0.95, wait_signal=True)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="wait", interpretation=interp, known_facts={}, gemini_available=True)
        assert d.response_intent == "ACK_WAIT"

    def test_decline_intent(self):
        interp = GeminiInterpretation(intent="decline", decline=True, confidence=0.95)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="stop", interpretation=interp, known_facts={}, gemini_available=True)
        assert d.decline

    def test_dates_question_unknown(self):
        interp = GeminiInterpretation(intent="question", question_type="dates", confidence=0.92)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="dates?", interpretation=interp, known_facts={}, gemini_available=True)
        assert "уточняю" in d.send_message or "confirming" in (d.send_message or "")

    def test_pets_question(self):
        interp = GeminiInterpretation(intent="question", question_type="pets", confidence=0.91)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="dog?", interpretation=interp, known_facts={}, gemini_available=True)
        assert d.send_message

    def test_nationality_question(self):
        interp = GeminiInterpretation(intent="question", question_type="nationality", confidence=0.91)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="citizenship?", interpretation=interp, known_facts={}, gemini_available=True)
        assert d.send_message

    def test_who_are_you(self):
        interp = GeminiInterpretation(intent="question", question_type="who_are_you", confidence=0.93)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="who", interpretation=interp, known_facts={}, gemini_available=True)
        assert "Open Home" in d.send_message

    def test_why_whatsapp(self):
        interp = GeminiInterpretation(intent="question", question_type="why_whatsapp", confidence=0.93)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="why wa", interpretation=interp, known_facts={}, gemini_available=True)
        assert d.send_message

    def test_owner_role(self):
        d = apply_inbound_policy(state=BusinessState.WAITING_ROLE, message="я собственник", interpretation=None, known_facts={}, gemini_available=True)
        assert d.save_role == "Владелец"

    def test_agent_role(self):
        d = apply_inbound_policy(state=BusinessState.WAITING_ROLE, message="I am agent", interpretation=None, known_facts={}, gemini_available=True)
        assert d.save_role == "Агент"

    def test_low_confidence(self):
        assert confidence_action(0.5) == "manual_review"

    def test_gemini_outage_manual(self):
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="complicated", interpretation=None, known_facts={}, gemini_available=False)
        assert d.manual_review

    def test_sanitize_rejects_bad_intent(self):
        i = sanitize_interpretation({"intent": "hack", "confidence": 2})
        assert i.intent == "other"
        assert i.confidence <= 1.0

    def test_only_allowed_intents(self):
        assert "hack" not in ALLOWED_INTENTS


class TestPolicy:
    def test_whatsapp_triggers_role(self):
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="+66812345678", interpretation=None, known_facts={}, gemini_available=True)
        assert d.save_whatsapp == "+66812345678"

    def test_wait_keeps_state(self):
        interp = GeminiInterpretation(intent="wait", confidence=0.95)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="wait", interpretation=interp, known_facts={}, gemini_available=True)
        assert d.new_state is None

    def test_known_fact(self):
        interp = GeminiInterpretation(intent="question", question_type="guests", confidence=0.95)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="guests?", interpretation=interp, known_facts={"guests": "4 guests"}, gemini_available=True)
        assert "4 guests" in d.send_message

    def test_unknown_fact_no_hallucination(self):
        interp = GeminiInterpretation(intent="question", question_type="budget", confidence=0.95)
        d = apply_inbound_policy(state=BusinessState.WAITING_CONTACT, message="budget?", interpretation=interp, known_facts={}, gemini_available=True)
        assert "уточняю" in d.send_message or "confirming" in d.send_message

    def test_unrelated_no_reset(self):
        d = apply_inbound_policy(state=BusinessState.WAITING_ROLE, message="hello", interpretation=GeminiInterpretation(intent="greeting", confidence=0.4), known_facts={}, gemini_available=True)
        assert d.manual_review or d.new_state is None

    def test_intro_villa(self):
        assert "вилла" in intro_template("Вилла").lower() or "villa" in intro_template("villa").lower()

    def test_hallucination_guard(self):
        assert reject_unsupported_generated_fact("Budget is 50000 THB", {})

    def test_dates_absent_guard(self):
        assert reject_unsupported_generated_fact("Dates 12.08-20.08", {})

    def test_pets_absent_guard(self):
        assert reject_unsupported_generated_fact("2 guests allowed", {"guests": None})

    def test_nationality_absent_guard(self):
        assert reject_unsupported_generated_fact("citizenship: Russian", {"nationality": None})

    def test_budget_absent_guard(self):
        assert reject_unsupported_generated_fact("50000 THB", {"budget": None})

    def test_guests_absent_guard(self):
        assert reject_unsupported_generated_fact("4 guests", {"guests": None})

    def test_malformed_cannot_trigger_action(self):
        i = sanitize_interpretation({"intent": "DROP TABLE", "confidence": 9})
        assert i.intent == "other"
