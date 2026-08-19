"""Knowledge entry and retrieval context models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class KnowledgeType(str, Enum):
    HARD_RULE = "HARD_RULE"
    HARD_RULE_REFERENCE = "HARD_RULE_REFERENCE"
    PLAYBOOK = "PLAYBOOK"
    STYLE = "STYLE"
    SAFETY = "SAFETY"
    EXAMPLE = "EXAMPLE"
    LEARNING = "LEARNING"
    SECTION = "SECTION"
    UNKNOWN = "UNKNOWN"


class KnowledgeStatus(str, Enum):
    CURRENT = "CURRENT"
    ADVISORY = "ADVISORY"
    TODO = "TODO"
    CANDIDATE = "CANDIDATE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AgentScope(str, Enum):
    AGENT6 = "AGENT6"
    AGENT7 = "AGENT7"
    BOTH = "BOTH"


@dataclass
class KnowledgeEntry:
    id: str
    type: str
    agents: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    title: str = ""
    content: str = ""
    priority: int = 50
    status: str = KnowledgeStatus.CURRENT.value
    section: str = ""
    source_refs: list[str] = field(default_factory=list)
    source_file: str = ""
    raw_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeEntry":
        return cls(
            id=str(data.get("id") or ""),
            type=str(data.get("type") or KnowledgeType.UNKNOWN.value),
            agents=[str(a).upper() for a in (data.get("agents") or [])],
            tags=[str(t).lower() for t in (data.get("tags") or [])],
            title=str(data.get("title") or ""),
            content=str(data.get("content") or ""),
            priority=int(data.get("priority") or 50),
            status=str(data.get("status") or KnowledgeStatus.CURRENT.value),
            section=str(data.get("section") or ""),
            source_refs=list(data.get("source_refs") or []),
            source_file=str(data.get("source_file") or ""),
            raw_meta=dict(data.get("raw_meta") or {}),
        )

    def applies_to(self, agent: str) -> bool:
        a = (agent or "").upper().replace(" ", "")
        if a in ("6", "AGENT6", "QUALIFIER"):
            a = "AGENT6"
        if a in ("7", "AGENT7", "ENVOY"):
            a = "AGENT7"
        scopes = {x.upper() for x in self.agents} or {"BOTH"}
        return "BOTH" in scopes or a in scopes or AgentScope.BOTH.value in scopes

    def compact_block(self, *, max_chars: int = 500) -> str:
        body = (self.content or "").strip()
        if len(body) > max_chars:
            body = body[: max_chars - 1].rstrip() + "…"
        header = f"{self.title or self.id}".strip()
        return f"{header}\n{body}".strip()

    @property
    def display_id(self) -> str:
        """Debug-friendly id with agent prefix if missing."""
        eid = self.id or ""
        if "/" in eid:
            return eid
        prefix = "shared"
        agents = {a.upper() for a in self.agents}
        if agents == {"AGENT6"}:
            prefix = "agent6"
        elif agents == {"AGENT7"}:
            prefix = "agent7"
        elif "BOTH" in agents or len(agents) > 1:
            prefix = "shared"
        return f"{prefix}/{eid}" if eid else prefix


@dataclass
class RetrievalContext:
    agent: str = "AGENT6"
    stage: str = ""
    intent: str = ""
    object_source: str = ""  # facebook | airbnb | unknown
    rental_policy: str = ""
    known_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    current_object: str = ""
    objection: str = ""
    situation: str = ""
    long_term: bool | None = None
    message_text: str = ""
    extra_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalResult:
    entries: list[KnowledgeEntry] = field(default_factory=list)
    selected_ids: list[str] = field(default_factory=list)
    fallback: bool = False
    reason: str = ""

    def compact_prompt(self, *, max_entries: int = 6, max_chars: int = 1800) -> str:
        if not self.entries:
            return ""
        parts: list[str] = []
        total = 0
        for e in self.entries[:max_entries]:
            block = e.compact_block()
            if total + len(block) > max_chars and parts:
                break
            parts.append(block)
            total += len(block)
        return "\n\n---\n\n".join(parts)


@dataclass
class ValidationIssue:
    file: str
    rule: str
    problem: str

    def human_message(self) -> str:
        return (
            f"Ошибка в файле:\n{self.file}\n\n"
            f"Правило:\n«{self.rule}»\n\n"
            f"Не заполнено / проблема:\n{self.problem}"
        )
