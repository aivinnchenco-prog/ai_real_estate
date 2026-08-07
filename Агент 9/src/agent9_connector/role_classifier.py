"""Deterministic owner/agent role classification."""

from __future__ import annotations

import re
from dataclasses import dataclass

OWNER_ALIASES = (
    r"\bowner\b", r"\bi am the owner\b", r"\bmy property\b", r"\bmy house\b",
    r"\bmy villa\b", r"собственник", r"владелец", r"хозяин", r"это мой объект",
    r"я собственник", r"я владелец",
)
AGENT_ALIASES = (
    r"\bagent\b", r"\bagency\b", r"\bbroker\b", r"\brealtor\b",
    r"real estate agent", r"я агент", r"\bагент\b", r"брокер", r"риелтор",
    r"представляю владельца", r"от имени владельца",
)


@dataclass(frozen=True)
class RoleResult:
    role: str  # owner | agent | unknown
    confidence: float
    source: str  # deterministic | gemini


def classify_role_deterministic(text: str) -> RoleResult:
    lowered = (text or "").lower()
    owner_hits = sum(1 for p in OWNER_ALIASES if re.search(p, lowered))
    agent_hits = sum(1 for p in AGENT_ALIASES if re.search(p, lowered))
    if owner_hits and not agent_hits:
        return RoleResult("owner", 0.98, "deterministic")
    if agent_hits and not owner_hits:
        return RoleResult("agent", 0.98, "deterministic")
    if owner_hits and agent_hits:
        if "агент" in lowered or "agent" in lowered:
            return RoleResult("agent", 0.75, "deterministic")
        return RoleResult("owner", 0.75, "deterministic")
    return RoleResult("unknown", 0.0, "deterministic")


def notion_role_value(role: str) -> str:
    return {"owner": "Владелец", "agent": "Агент"}.get(role, "Не определено")
