"""Parse human-editable Agent6/Agent7 knowledge markdown files.

Human format (preferred):
  ## Title
  Ситуация: ...
  Как должен действовать агент: ...

Technical metadata (id/agents/tags/priority/status) is generated automatically.
Fail-safe: malformed rules are isolated; never raise to callers.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Iterable

from agent6_qualifier.knowledge.models import (
    KnowledgeEntry,
    KnowledgeStatus,
    KnowledgeType,
    ValidationIssue,
)

logger = logging.getLogger(__name__)

AGENT6_FILE = "БАЗА_ЗНАНИЙ_AGENT_6_КЛИЕНТЫ.md"
AGENT7_FILE = "БАЗА_ЗНАНИЙ_AGENT_7_ВЛАДЕЛЬЦЫ.md"

_SECTION_RE = re.compile(r"^#\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
_SECTION_NAMED_RE = re.compile(r"^#\s+(МОИ НОВЫЕ ПРАВИЛА)\s*$", re.MULTILINE | re.IGNORECASE)
_ENTRY_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_ENTRY_H3_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_SOURCE_PROJECT_RE = re.compile(
    r"(?:\*\*)?(?:Source in project|Источник в коде|Источник истины(?: в коде)?):?\*?\*?\s*`?([^`\n]+)`?",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|bearer\s+[a-z0-9]|sk-[a-z0-9]{10,})"
)

# Optional legacy meta (still accepted, not required for humans).
_META_LINE_RE = re.compile(
    r"^\*\*(id|type|agents|tags|priority|status|source):?\*\*:?\s*`?([^`\n]+?)`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TYPE_MARKER_RE = re.compile(
    r"\[(HARD RULE|HARD_RULE_REFERENCE|PLAYBOOK|STYLE|SAFETY|EXAMPLE|LEARNING)\]",
    re.IGNORECASE,
)

TYPE_MAP = {
    "HARD RULE": KnowledgeType.HARD_RULE_REFERENCE.value,
    "HARD_RULE": KnowledgeType.HARD_RULE_REFERENCE.value,
    "HARD_RULE_REFERENCE": KnowledgeType.HARD_RULE_REFERENCE.value,
    "PLAYBOOK": KnowledgeType.PLAYBOOK.value,
    "STYLE": KnowledgeType.STYLE.value,
    "SAFETY": KnowledgeType.SAFETY.value,
    "EXAMPLE": KnowledgeType.EXAMPLE.value,
    "LEARNING": KnowledgeType.LEARNING.value,
}

TAG_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("facebook", ("facebook", "fb marketplace", "fb_", "marketplace")),
    ("airbnb", ("airbnb",)),
    ("long_term", ("долгосроч", "long-term", "long_term", "на год", "12 мес", "6 месяц")),
    ("short_stay", ("коротк", "3 месяц", "на месяц", "short")),
    ("rental_policy", ("rental policy", "политик", "минимум 6")),
    ("qualification", ("квалификац", "критери", "поля")),
    ("first_contact", ("первый контакт", "first contact", "ищу вилл")),
    ("specific_property", ("конкретн", "объект id", "specific", "интересует объект")),
    ("matching", ("подбор", "matching", "альтернатив", "вариант")),
    ("price", ("цен", "price", "дорог", "скидк")),
    ("budget", ("бюджет", "budget")),
    ("objection", ("возражен", "objection")),
    ("airbnb_comparison", ("на airbnb дешев", "сравнивает с airbnb")),
    ("unavailable", ("недоступ", "не подходит", "занят")),
    ("owner_check", ("владельц", "owner check", "проверк")),
    ("handoff", ("передач", "agent7", "handoff")),
    ("ownership", ("human handoff", "bot_active", "ownership", "менеджер")),
    ("contact_role", ("роль", "client", "owner", "agent", "contact")),
    ("correlation", ("correlation", "корреляц", "owner_request")),
    ("idempotency", ("повтор", "duplicate", "idempot")),
    ("busy", ("занято", "busy")),
    ("free", ("свободно", "free")),
    ("conditions_changed", ("изменил", "conditions_changed", "новая цена")),
    ("style", ("стиль", "тон", "общен")),
    ("safety", ("нельзя", "не обещать", "safety")),
    ("think_later", ("подумаю",)),
    ("send_options", ("пришлите вариант", "покажите вариант")),
    ("no_budget", ("без бюджета", "бюджета нет", "не хочу говорить бюджет")),
]


def knowledge_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "knowledge"


def default_master_path() -> Path:
    """Legacy redirect stub path (not a competing source of truth)."""
    return knowledge_dir() / "БАЗА_ЗНАНИЙ.md"


def agent6_human_path() -> Path:
    return knowledge_dir() / AGENT6_FILE


def agent7_human_path() -> Path:
    return knowledge_dir() / AGENT7_FILE


def human_knowledge_paths() -> list[Path]:
    return [agent6_human_path(), agent7_human_path()]


def agent_for_path(path: Path | str) -> str:
    name = Path(path).name
    if "AGENT_7" in name.upper() or "ВЛАДЕЛЬЦ" in name.upper():
        return "AGENT7"
    if "AGENT_6" in name.upper() or "КЛИЕНТ" in name.upper():
        return "AGENT6"
    return "AGENT6"


def id_prefix_for_agent(agent: str) -> str:
    a = (agent or "AGENT6").upper()
    if a == "AGENT7":
        return "agent7"
    if a == "BOTH":
        return "shared"
    return "agent6"


def _slugify(title: str) -> str:
    raw = (title or "").strip().lower()
    raw = re.sub(r"[^\w\s\-а-яё]+", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"[\s_]+", "-", raw).strip("-")
    return raw[:72] or "entry"


def _stable_id(agent: str, title: str, section: str = "") -> str:
    slug = _slugify(title)
    prefix = id_prefix_for_agent(agent)
    base = f"{prefix}/{slug}"
    # Collision-resistant short hash from title+section (stable).
    digest = hashlib.sha1(f"{agent}|{section}|{title}".encode("utf-8")).hexdigest()[:6]
    return f"{base}-{digest}" if len(slug) < 3 else base


def _extract_tags(title: str, content: str, section: str = "") -> list[str]:
    blob = f"{title}\n{section}\n{content}".lower()
    tags: list[str] = []
    for tag, keys in TAG_KEYWORDS:
        if any(k in blob for k in keys):
            tags.append(tag)
    if "мои новые" in section.lower():
        tags.append("manual")
    return tags or ["general"]


def _infer_type(title: str, content: str, section: str = "") -> str:
    blob = f"{title}\n{content}\n{section}".lower()
    m = _TYPE_MARKER_RE.search(content)
    if m:
        return TYPE_MAP.get(m.group(1).upper(), KnowledgeType.PLAYBOOK.value)
    if any(
        x in blob
        for x in (
            "источник истины",
            "source in project",
            "источник в коде",
            "жёстк",
            "hard rule",
            "нельзя изменить markdown",
        )
    ):
        return KnowledgeType.HARD_RULE_REFERENCE.value
    if any(x in blob for x in ("стиль общения", "тон", "писать естественно")):
        return KnowledgeType.STYLE.value
    if any(
        x in blob
        for x in (
            "human_handoff",
            "нельзя обещать",
            "client != owner",
            "idempotency",
            "correlation",
            "safety",
        )
    ):
        return KnowledgeType.SAFETY.value
    if "пример" in title.lower() or section.lower().startswith("30.") or "пример" in section.lower():
        return KnowledgeType.EXAMPLE.value
    return KnowledgeType.PLAYBOOK.value


def _infer_priority(entry_type: str, tags: list[str], section: str) -> int:
    if entry_type in {
        KnowledgeType.HARD_RULE_REFERENCE.value,
        KnowledgeType.SAFETY.value,
    }:
        return 95
    if "manual" in tags or "мои новые" in section.lower():
        return 55
    if entry_type == KnowledgeType.STYLE.value:
        return 70
    return 50


def _has_action(block: str) -> bool:
    return bool(
        re.search(
            r"(?im)^(как должен действовать агент|как действовать|действие)\s*:",
            block,
        )
    ) or bool(re.search(r"(?im)как должен действовать агент\s*:", block))


def _has_situation(block: str) -> bool:
    return bool(re.search(r"(?im)^(ситуация)\s*:", block)) or "Ситуация:" in block


def _parse_meta(block: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for m in _META_LINE_RE.finditer(block):
        meta[m.group(1).lower()] = m.group(2).strip()
    return meta


def _source_refs(block: str) -> list[str]:
    return [m.group(1).strip() for m in _SOURCE_PROJECT_RE.finditer(block)]


def _section_spans(text: str) -> list[tuple[int, int, str, str]]:
    spans: list[tuple[int, int, str, str]] = []
    numeric = list(_SECTION_RE.finditer(text))
    named = list(_SECTION_NAMED_RE.finditer(text))
    all_m = sorted(numeric + named, key=lambda m: m.start())
    for i, m in enumerate(all_m):
        start = m.start()
        end = all_m[i + 1].start() if i + 1 < len(all_m) else len(text)
        if m.re is _SECTION_NAMED_RE or (m.lastindex == 1 and "МОИ" in m.group(0).upper()):
            label = "МОИ НОВЫЕ ПРАВИЛА"
            num = "manual"
        else:
            num = m.group(1)
            label = f"{num}. {m.group(2).strip()}"
        spans.append((start, end, str(num), label))
    return spans


def parse_human_markdown(
    text: str,
    *,
    agent: str,
    source_file: str = "",
) -> tuple[list[KnowledgeEntry], list[ValidationIssue]]:
    """Parse one human knowledge file. Returns entries + human validation issues."""
    if not text or not text.strip():
        return [], []

    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    agent_u = (agent or "AGENT6").upper()
    prefix = id_prefix_for_agent(agent_u)
    entries: list[KnowledgeEntry] = []
    issues: list[ValidationIssue] = []
    seen: set[str] = set()

    spans = _section_spans(cleaned)
    if not spans:
        # Whole file as one virtual section.
        spans = [(0, len(cleaned), "0", "Общее")]

    for start, end, num, section_label in spans:
        body = cleaned[start:end]
        # Prefer ## entries (simple UX). Fall back to ### (legacy).
        heads = list(_ENTRY_H2_RE.finditer(body))
        # Skip document title "## AGENT 6 — ..." under the very top if no situation body.
        # Keep only ## that look like rules (inside numbered sections / manual).
        use_h3 = False
        if not heads:
            heads = list(_ENTRY_H3_RE.finditer(body))
            use_h3 = True

        for j, hm in enumerate(heads):
            try:
                e_start = hm.start()
                e_end = heads[j + 1].start() if j + 1 < len(heads) else len(body)
                block = body[e_start:e_end].strip()
                heading = hm.group(1).strip()
                # Skip top-level agent banner headings.
                if heading.upper().startswith("AGENT 6") or heading.upper().startswith("AGENT 7"):
                    continue
                if heading.upper().startswith("БАЗА ЗНАНИЙ"):
                    continue

                meta = _parse_meta(block)
                # Validate simple human rules (skip pure banners / tiny stubs).
                is_manual = "мои новые" in section_label.lower()
                looks_like_rule = (
                    _has_situation(block)
                    or _has_action(block)
                    or use_h3
                    or bool(meta)
                    or "Источник" in block
                    or "Source in project" in block
                )
                if not looks_like_rule and len(block) < 80:
                    continue

                if is_manual or _has_situation(block) or _has_action(block):
                    if not _has_action(block):
                        issues.append(
                            ValidationIssue(
                                file=source_file or "knowledge",
                                rule=heading,
                                problem="«Как должен действовать агент»",
                            )
                        )
                        # Isolate: still index if situation exists, but mark low priority.
                    if not _has_situation(block) and is_manual:
                        issues.append(
                            ValidationIssue(
                                file=source_file or "knowledge",
                                rule=heading,
                                problem="«Ситуация»",
                            )
                        )

                eid = (meta.get("id") or "").strip() or _stable_id(
                    agent_u, heading, section_label
                )
                if not eid.startswith(("agent6/", "agent7/", "shared/")):
                    eid = f"{prefix}/{eid}"
                if eid in seen:
                    eid = f"{eid}-{j}"

                tags = _extract_tags(heading, block, section_label)
                if meta.get("tags"):
                    tags = list(
                        dict.fromkeys(
                            tags
                            + [t.strip().lower() for t in re.split(r"[,;\s]+", meta["tags"]) if t.strip()]
                        )
                    )
                entry_type = (
                    TYPE_MAP.get(meta["type"].upper().replace(" ", "_"), None)
                    if meta.get("type")
                    else None
                ) or _infer_type(heading, block, section_label)

                status = (meta.get("status") or "").upper() or (
                    KnowledgeStatus.APPROVED.value
                    if is_manual
                    else KnowledgeStatus.CURRENT.value
                )
                priority = int(meta.get("priority") or _infer_priority(entry_type, tags, section_label))

                # File decides agent — ignore manual agents metadata for isolation.
                agents = [agent_u]
                # Shared safety tags duplicated in both files stay agent-scoped by file.
                # Optional: mark shared if section says so — still agent from file.

                entries.append(
                    KnowledgeEntry(
                        id=eid,
                        type=entry_type,
                        agents=agents,
                        tags=tags,
                        title=heading,
                        content=block,
                        priority=priority,
                        status=status,
                        section=section_label,
                        source_refs=_source_refs(block),
                        source_file=source_file or "",
                        raw_meta=meta,
                    )
                )
                seen.add(eid)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "KNOWLEDGE_FALLBACK malformed_entry file=%s section=%s err=%s",
                    source_file,
                    section_label,
                    type(exc).__name__,
                )
                issues.append(
                    ValidationIssue(
                        file=source_file or "knowledge",
                        rule="(не разобрано)",
                        problem=f"правило пропущено из‑за ошибки разбора ({type(exc).__name__})",
                    )
                )
                continue

    return entries, issues


def parse_master_markdown(text: str, *, agent: str = "BOTH", source_file: str = "") -> list[KnowledgeEntry]:
    """Backward-compatible wrapper."""
    entries, _issues = parse_human_markdown(text, agent=agent, source_file=source_file)
    return entries


def load_file_entries(path: Path) -> tuple[list[KnowledgeEntry], list[ValidationIssue]]:
    if not path.exists():
        logger.warning("KNOWLEDGE_FALLBACK missing_file path=%s", path)
        return [], [
            ValidationIssue(file=path.name, rule="(файл)", problem="файл не найден")
        ]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("KNOWLEDGE_FALLBACK read_error err=%s", type(exc).__name__)
        return [], [
            ValidationIssue(
                file=path.name, rule="(файл)", problem=f"не удалось прочитать ({type(exc).__name__})"
            )
        ]
    agent = agent_for_path(path)
    try:
        return parse_human_markdown(text, agent=agent, source_file=path.name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("KNOWLEDGE_FALLBACK parse_error err=%s", type(exc).__name__)
        return [], [
            ValidationIssue(
                file=path.name, rule="(файл)", problem=f"ошибка разбора ({type(exc).__name__})"
            )
        ]


def load_all_human_entries() -> tuple[list[KnowledgeEntry], list[ValidationIssue]]:
    all_entries: list[KnowledgeEntry] = []
    all_issues: list[ValidationIssue] = []
    for path in human_knowledge_paths():
        entries, issues = load_file_entries(path)
        all_entries.extend(entries)
        all_issues.extend(issues)
    # Deduplicate by id (first wins).
    seen: set[str] = set()
    unique: list[KnowledgeEntry] = []
    for e in all_entries:
        if e.id in seen:
            continue
        seen.add(e.id)
        unique.append(e)
    return unique, all_issues


def load_master_entries(path: Path | None = None) -> list[KnowledgeEntry]:
    """Load knowledge entries.

    If path points to a single file — parse that file.
    Otherwise load both human files (preferred).
    """
    if path is not None:
        entries, _ = load_file_entries(path)
        return entries
    entries, _ = load_all_human_entries()
    if entries:
        return entries
    # Legacy fallback if new files missing but old master still has content.
    legacy = default_master_path()
    if legacy.exists():
        text = legacy.read_text(encoding="utf-8")
        # Redirect stub has almost no rules.
        if "разделена на два файла" in text.lower() or len(text) < 500:
            return []
        parsed, _ = parse_human_markdown(text, agent="BOTH", source_file=legacy.name)
        return parsed
    return []


def find_secrets_in_entries(entries: Iterable[KnowledgeEntry]) -> list[str]:
    hits: list[str] = []
    for e in entries:
        blob = f"{e.id}\n{e.title}\n{e.content}"
        if _SECRET_RE.search(blob):
            hits.append(e.id)
    return hits


def validate_human_files() -> list[ValidationIssue]:
    """Return human-readable validation issues for both files."""
    _entries, issues = load_all_human_entries()
    # Hard validation: manual rules must have action.
    hard: list[ValidationIssue] = []
    for issue in issues:
        if issue.problem.startswith("«Как должен"):
            hard.append(issue)
        elif issue.problem.startswith("«Ситуация»"):
            hard.append(issue)
        elif "файл не найден" in issue.problem:
            hard.append(issue)
    return hard or issues
