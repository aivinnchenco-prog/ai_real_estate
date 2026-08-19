"""Gemini structured interpreter + approved response phrasing."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from .config_loader import confidence_thresholds

ALLOWED_INTENTS = frozenset({
    "whatsapp_provided", "role_answer", "question", "wait", "decline",
    "greeting", "other",
})
ALLOWED_QUESTION_TYPES = frozenset({
    "dates", "pets", "nationality", "guests", "budget", "duration", "price",
    "availability", "commission", "viewing", "why_whatsapp", "who_are_you", "other",
})
ALLOWED_ROLES = frozenset({"owner", "agent", "unknown"})
ALLOWED_RESPONSE_INTENTS = frozenset({
    "ACK_WAIT", "ANSWER_KNOWN_FACT", "ANSWER_UNKNOWN_FACT",
    "ASK_WHATSAPP", "ASK_ROLE", "DECLINE_ACK",
})


@dataclass
class GeminiInterpretation:
    intent: str = "other"
    question_type: str | None = None
    whatsapp: str | None = None
    role: str = "unknown"
    language: str = "en"
    wait_signal: bool = False
    decline: bool = False
    needs_reply: bool = False
    confidence: float = 0.0
    offers_owner_contact: bool = False
    raw: dict = field(default_factory=dict)


class GeminiClient:
    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("AGENT9_GEMINI_API_KEY", "")
        self.model = model or os.getenv("AGENT9_GEMINI_MODEL", "gemini-2.0-flash")
        self.available = bool(self.api_key)

    def _endpoint(self) -> str:
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )

    def _call(self, prompt: str) -> str:
        if not self.available:
            raise RuntimeError("gemini_unavailable")
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        r = requests.post(self._endpoint(), json=payload, timeout=45)
        r.raise_for_status()
        data = r.json()
        parts = data["candidates"][0]["content"]["parts"]
        return parts[0].get("text", "")

    def interpret_inbound(self, message: str, context: dict) -> GeminiInterpretation:
        prompt = (
            "You are a classifier for Facebook Messenger outreach. "
            "Return ONLY JSON with keys: intent, question_type, whatsapp, role, language, "
            "wait_signal, decline, needs_reply, confidence, offers_owner_contact.\n"
            f"Allowed intent: {sorted(ALLOWED_INTENTS)}\n"
            f"Allowed question_type: {sorted(ALLOWED_QUESTION_TYPES)}\n"
            f"Allowed role: {sorted(ALLOWED_ROLES)}\n"
            f"Context: {json.dumps(context, ensure_ascii=False)}\n"
            f"Message: {message}"
        )
        raw_text = self._call(prompt)
        parsed = parse_structured_json(raw_text)
        if parsed is None:
            raw_text = self._call(prompt + "\nReturn valid JSON only.")
            parsed = parse_structured_json(raw_text)
        if parsed is None:
            raise ValueError("invalid_json_twice")
        return sanitize_interpretation(parsed)

    def phrase_response(
        self,
        *,
        response_intent: str,
        language: str,
        known_facts: dict,
        template_hint: str,
    ) -> str:
        if not self.available:
            return template_hint
        prompt = (
            "Rephrase the approved response in 1-3 short sentences. "
            "Do NOT add new facts, promises, prices, dates, or questions except allowed.\n"
            f"response_intent={response_intent}\n"
            f"language={language}\n"
            f"known_facts={json.dumps(known_facts, ensure_ascii=False)}\n"
            f"template_hint={template_hint}"
        )
        try:
            text = self._call(prompt)
            obj = parse_structured_json(text)
            if isinstance(obj, dict) and obj.get("text"):
                return str(obj["text"])[:500]
            return text.strip()[:500] or template_hint
        except Exception:
            return template_hint


def parse_structured_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def sanitize_interpretation(data: dict) -> GeminiInterpretation:
    intent = str(data.get("intent") or "other")
    if intent not in ALLOWED_INTENTS:
        intent = "other"
    qtype = data.get("question_type")
    if qtype and qtype not in ALLOWED_QUESTION_TYPES:
        qtype = "other"
    role = str(data.get("role") or "unknown")
    if role not in ALLOWED_ROLES:
        role = "unknown"
    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    return GeminiInterpretation(
        intent=intent,
        question_type=qtype,
        whatsapp=data.get("whatsapp"),
        role=role,
        language=str(data.get("language") or "en")[:2].lower(),
        wait_signal=bool(data.get("wait_signal")),
        decline=bool(data.get("decline")),
        needs_reply=bool(data.get("needs_reply")),
        confidence=max(0.0, min(1.0, confidence)),
        offers_owner_contact=bool(data.get("offers_owner_contact")),
        raw=data,
    )


def confidence_action(confidence: float) -> str:
    auto_min, recheck_min = confidence_thresholds()
    if confidence >= auto_min:
        return "auto"
    if confidence >= recheck_min:
        return "recheck"
    return "manual_review"
