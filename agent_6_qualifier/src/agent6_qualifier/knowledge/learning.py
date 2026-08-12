"""Learning candidates + events. No automatic self-learning into production."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

CandidateStatus = Literal["CANDIDATE", "APPROVED", "REJECTED"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_candidates_path() -> Path:
    return Path(__file__).resolve().parents[3] / "knowledge" / "candidate_patterns.json"


def default_candidates_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "knowledge" / "candidates"


def mask_phone(raw: str | None) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}***{digits[-2:]}"


def hash_phone(raw: str | None) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return ""
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()[:16]


@dataclass
class LearningEvent:
    conversation_id: str
    agent: str
    stage: str = ""
    client_or_owner_message: str = ""
    agent_response: str = ""
    strategy: str = ""
    knowledge_refs: list[str] = field(default_factory=list)
    result: str = ""
    next_turn_received: bool | None = None
    owner_check_reached: bool | None = None
    booking_reached: bool | None = None
    handoff: bool | None = None
    notes: str = ""
    phone_hash: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Never persist raw phones in events.
        return d


@dataclass
class CandidatePattern:
    id: str
    agent: str
    situation: str
    observed_problem: str
    suggested_principle: str
    supporting_conversations: list[str] = field(default_factory=list)
    confidence: float = 0.5
    status: CandidateStatus = "CANDIDATE"
    created_at: str = ""
    reviewed_at: str = ""
    review_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidatePattern":
        return cls(
            id=str(data.get("id") or ""),
            agent=str(data.get("agent") or "AGENT6"),
            situation=str(data.get("situation") or ""),
            observed_problem=str(data.get("observed_problem") or ""),
            suggested_principle=str(data.get("suggested_principle") or ""),
            supporting_conversations=list(data.get("supporting_conversations") or []),
            confidence=float(data.get("confidence") or 0.5),
            status=str(data.get("status") or "CANDIDATE"),  # type: ignore[arg-type]
            created_at=str(data.get("created_at") or ""),
            reviewed_at=str(data.get("reviewed_at") or ""),
            review_notes=str(data.get("review_notes") or ""),
        )


class CandidateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_candidates_path()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"candidates": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"candidates": {}}
        if not isinstance(data, dict):
            return {"candidates": {}}
        if not isinstance(data.get("candidates"), dict):
            data["candidates"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tmp.replace(self.path)

    def list(self, status: str | None = None) -> list[CandidatePattern]:
        data = self._read()
        out = [CandidatePattern.from_dict(v) for v in data["candidates"].values()]
        if status:
            out = [c for c in out if c.status == status]
        return sorted(out, key=lambda c: c.created_at or c.id)

    def get(self, cid: str) -> CandidatePattern | None:
        data = self._read()
        raw = data["candidates"].get(cid)
        return CandidatePattern.from_dict(raw) if raw else None

    def add(self, candidate: CandidatePattern) -> CandidatePattern:
        if not candidate.id:
            candidate.id = f"cand-{uuid.uuid4().hex[:10]}"
        if not candidate.created_at:
            candidate.created_at = _now()
        data = self._read()
        data["candidates"][candidate.id] = candidate.to_dict()
        self._write(data)
        return candidate

    def set_status(
        self, cid: str, status: CandidateStatus, *, notes: str = ""
    ) -> CandidatePattern | None:
        data = self._read()
        raw = data["candidates"].get(cid)
        if not raw:
            return None
        raw["status"] = status
        raw["reviewed_at"] = _now()
        if notes:
            raw["review_notes"] = notes
        data["candidates"][cid] = raw
        self._write(data)
        return CandidatePattern.from_dict(raw)


def recommended_destination(candidate: CandidatePattern) -> str:
    """Human-facing destination for an approved candidate."""
    agent = (candidate.agent or "").upper()
    blob = f"{candidate.situation} {candidate.suggested_principle} {candidate.observed_problem}".lower()
    ownerish = any(
        x in blob
        for x in (
            "owner",
            "владельц",
            "агент объекта",
            "availability",
            "busy",
            "conditions_changed",
            "outreach",
            "owner_request",
        )
    )
    if agent in {"AGENT7", "7", "ENVOY"} or (ownerish and agent not in {"AGENT6", "6"}):
        return "AGENT7 OWNER KNOWLEDGE"
    return "AGENT6 CLIENT KNOWLEDGE"


def append_approved_playbook_to_master(
    candidate: CandidatePattern,
    *,
    master_path: Path | None = None,
) -> str:
    """Append APPROVED rule into the correct human file. Never touches business code."""
    from agent6_qualifier.knowledge.parser import agent6_human_path, agent7_human_path

    dest = recommended_destination(candidate)
    if master_path is not None:
        path = master_path
    elif dest.startswith("AGENT7"):
        path = agent7_human_path()
    else:
        path = agent6_human_path()

    title = (candidate.situation or candidate.id or "Новое правило").strip()
    block = f"""
## {title}

Ситуация:
{candidate.situation or "—"}
Проблема, которую заметили: {candidate.observed_problem or "—"}

Как должен действовать агент:
{candidate.suggested_principle or "—"}

Что нельзя делать:
Не превращать candidate в hard business rule без правки кода.
Не использовать отклонённые правила.

"""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    marker = "# МОИ НОВЫЕ ПРАВИЛА"
    if marker in text:
        text = text.rstrip() + "\n" + block
    else:
        text = text.rstrip() + "\n\n" + marker + "\n" + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"{'agent7' if dest.startswith('AGENT7') else 'agent6'}/{candidate.id}"


def is_hard_rule_candidate(candidate: CandidatePattern) -> bool:
    text = f"{candidate.situation} {candidate.suggested_principle}".lower()
    markers = (
        "hard rule",
        "минимум",
        "minimum",
        "rental_policy",
        "обязательно",
        "must not",
        "нельзя менять код",
        "fb_min",
        "6 месяц",
    )
    return any(m in text for m in markers)
