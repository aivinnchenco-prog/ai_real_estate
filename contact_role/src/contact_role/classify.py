"""Message classification candidate API (does not write mirrors directly)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .assign import AssignResult, assign_known_role
from .roles import CanonicalRole
from .sources import RoleSource
from .state import ContactRoleStore

_CLIENT_RE = re.compile(
    r"(ищ[уем]|сниму|арендовать|снять|looking for|want to rent|need (a |an )?(villa|apartment|condo)|"
    r"бюджет|до \d|bang tao|на месяц|"
    r"интересует объект|интересует вилл|интересует апартамент|по объекту|"
    r"interested in (the )?(object|villa|listing)|"
    r"\b(?:[A-Za-z]{1,3}_)?\d{8}_\d{3}\b)",
    re.I,
)
_OWNER_RE = re.compile(
    r"(у меня есть|хочу сдать|сда[юм]|собственник|владелец|my (villa|house|property)|"
    r"i (am|'m) the owner|актуальн)",
    re.I,
)
_AGENT_RE = re.compile(
    r"(я агент|брокер|риелтор|\bagent\b|\bbroker\b|от имени владельца|"
    r"представляю владельца|есть объект от собственника)",
    re.I,
)


@dataclass
class ClassificationResult:
    candidate_role: str
    confidence: float
    source: str = RoleSource.MESSAGE_CLASSIFICATION.value
    reason: str = ""
    applied: AssignResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_role": self.candidate_role,
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
            "applied": self.applied.to_dict() if self.applied else None,
        }


def classify_text_role(text: str | None) -> ClassificationResult:
    """Heuristic classifier — returns a candidate only (no persistence)."""
    body = (text or "").strip()
    if not body:
        return ClassificationResult(
            candidate_role=CanonicalRole.UNKNOWN.value,
            confidence=0.0,
            reason="empty",
        )

    client_hit = bool(_CLIENT_RE.search(body))
    owner_hit = bool(_OWNER_RE.search(body))
    agent_hit = bool(_AGENT_RE.search(body))

    # Agent phrases win over owner/client when present.
    if agent_hit:
        return ClassificationResult(
            CanonicalRole.AGENT.value,
            0.9 if not owner_hit else 0.7,
            reason="agent_keywords" if not owner_hit else "agent_over_owner_ambiguous",
        )
    # Strong owner intent beats generic location/client keywords.
    if owner_hit:
        return ClassificationResult(
            CanonicalRole.OWNER.value,
            0.9 if not client_hit else 0.8,
            reason="owner_keywords" if not client_hit else "owner_over_client_ambiguous",
        )
    if client_hit:
        return ClassificationResult(
            CanonicalRole.CLIENT.value, 0.85, reason="client_keywords"
        )
    return ClassificationResult(
        CanonicalRole.UNKNOWN.value, 0.0, reason="ambiguous"
    )


def classify_unknown_contact_role(
    *,
    phone: str | None = None,
    text: str | None = None,
    contact_key: str | None = None,
    store: ContactRoleStore | None = None,
    allow_reevaluation: bool = False,
    persist: bool = True,
    enqueue_sync: bool = True,
    **assign_kwargs: Any,
) -> ClassificationResult:
    """Classify only when role is UNKNOWN (or re-evaluation allowed).

    LLM/heuristic must NOT write amoCRM / WhatsApp directly — only return
    a candidate; persistence goes through role policy + dual sync.
    """
    store_u = store or ContactRoleStore()
    from .phone import contact_key_for

    key = contact_key_for(phone=phone, contact_key=contact_key)
    current = None
    if key:
        current = store_u.get(key)
    if current is None and phone:
        current = store_u.get_by_phone(phone)

    cur_role = (
        CanonicalRole.parse(current.canonical_role)
        if current
        else CanonicalRole.UNKNOWN
    )
    if cur_role is not CanonicalRole.UNKNOWN and not allow_reevaluation:
        return ClassificationResult(
            candidate_role=cur_role.value,
            confidence=1.0,
            source=current.role_source if current else RoleSource.UNKNOWN.value,
            reason="already_known_skip_classifier",
        )

    candidate = classify_text_role(text)
    if (
        CanonicalRole.parse(candidate.candidate_role) is CanonicalRole.UNKNOWN
        or not persist
    ):
        return candidate

    applied = assign_known_role(
        phone=phone,
        contact_key=contact_key,
        role=candidate.candidate_role,
        source=RoleSource.MESSAGE_CLASSIFICATION,
        confidence=candidate.confidence,
        store=store_u,
        allow_reevaluation=allow_reevaluation,
        enqueue_sync=enqueue_sync,
        trigger="classify_unknown_contact_role",
        **assign_kwargs,
    )
    candidate.applied = applied
    if not applied.applied and applied.reason == "priority_blocked":
        candidate.reason = "priority_blocked"
    return candidate
