"""Build / load knowledge_index.json from Agent6 + Agent7 human files."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from agent6_qualifier.knowledge.models import KnowledgeEntry, ValidationIssue
from agent6_qualifier.knowledge.parser import (
    find_secrets_in_entries,
    human_knowledge_paths,
    load_all_human_entries,
    load_master_entries,
    validate_human_files,
)

logger = logging.getLogger(__name__)


def default_index_path() -> Path:
    return Path(__file__).resolve().parents[3] / "knowledge" / "knowledge_index.json"


def last_good_index_path(index_path: Path | None = None) -> Path:
    base = index_path or default_index_path()
    return base.with_name("knowledge_index.last_good.json")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _count_by_agent(entries: list[KnowledgeEntry]) -> dict[str, int]:
    out = {"AGENT6": 0, "AGENT7": 0, "OTHER": 0}
    for e in entries:
        agents = {a.upper() for a in (e.agents or [])}
        if agents == {"AGENT6"}:
            out["AGENT6"] += 1
        elif agents == {"AGENT7"}:
            out["AGENT7"] += 1
        else:
            out["OTHER"] += 1
    return out


def rebuild_index(
    *,
    master_path: Path | None = None,
    index_path: Path | None = None,
    require_valid: bool = False,
) -> dict[str, Any]:
    """Rebuild index from both human files (or a single legacy path)."""
    out = index_path or default_index_path()
    issues: list[ValidationIssue] = []

    if master_path is not None:
        entries = load_master_entries(master_path)
        sources = [master_path.name]
    else:
        entries, issues = load_all_human_entries()
        sources = [p.name for p in human_knowledge_paths() if p.exists()]
        hard = validate_human_files()
        # Blocking only when action missing on manual rules / missing files.
        blocking = [
            i
            for i in hard
            if i.problem.startswith("«Как должен")
            or "файл не найден" in i.problem
        ]
        if require_valid and blocking:
            raise ValueError("; ".join(i.human_message() for i in blocking[:3]))

    secrets = find_secrets_in_entries(entries)
    if secrets:
        cleaned: list[KnowledgeEntry] = []
        for e in entries:
            if e.id in secrets:
                logger.warning("KNOWLEDGE_FALLBACK secret_stripped id=%s", e.id)
                cleaned.append(
                    KnowledgeEntry(
                        id=e.id,
                        type=e.type,
                        agents=e.agents,
                        tags=e.tags,
                        title=e.title,
                        content="[REDACTED — удалите секреты из базы знаний]",
                        priority=e.priority,
                        status=e.status,
                        section=e.section,
                        source_refs=e.source_refs,
                        source_file=e.source_file,
                    )
                )
            else:
                cleaned.append(e)
        entries = cleaned

    counts = _count_by_agent(entries)
    payload = {
        "version": 2,
        "sources": sources,
        "source": ", ".join(sources),
        "entry_count": len(entries),
        "agent6_count": counts["AGENT6"],
        "agent7_count": counts["AGENT7"],
        "validation_warnings": [
            {"file": i.file, "rule": i.rule, "problem": i.problem} for i in issues[:50]
        ],
        "entries": [e.to_dict() for e in entries],
    }

    # Preserve last-good before overwrite when current index looks healthy.
    if out.exists():
        try:
            prev = json.loads(out.read_text(encoding="utf-8"))
            if int(prev.get("entry_count") or 0) > 0 and prev.get("entries"):
                shutil.copyfile(out, last_good_index_path(out))
        except Exception:
            pass

    if not entries:
        # Keep last-good if rebuild produced empty catalog.
        lg = last_good_index_path(out)
        if lg.exists():
            logger.warning("KNOWLEDGE_FALLBACK empty_rebuild_keeping_last_good")
            try:
                return json.loads(lg.read_text(encoding="utf-8"))
            except Exception:
                pass

    _write_json(out, payload)
    # Promote successful rebuild to last-good.
    if entries:
        try:
            shutil.copyfile(out, last_good_index_path(out))
        except Exception:
            pass
    return payload


def load_index(index_path: Path | None = None) -> list[KnowledgeEntry]:
    path = index_path or default_index_path()
    # Optional safe mtime rebuild (never breaks callers).
    if index_path is None:
        try:
            maybe_rebuild_if_sources_changed()
        except Exception:
            pass
    if not path.exists():
        try:
            payload = rebuild_index(index_path=path)
            return [KnowledgeEntry.from_dict(x) for x in payload.get("entries") or []]
        except Exception as exc:  # noqa: BLE001
            logger.warning("KNOWLEDGE_FALLBACK index_rebuild err=%s", type(exc).__name__)
            lg = last_good_index_path(path)
            if lg.exists():
                try:
                    data = json.loads(lg.read_text(encoding="utf-8"))
                    return [KnowledgeEntry.from_dict(x) for x in data.get("entries") or []]
                except Exception:
                    pass
            entries, _ = load_all_human_entries()
            return entries
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [KnowledgeEntry.from_dict(x) for x in (data.get("entries") or [])]
        if entries:
            return entries
    except Exception as exc:  # noqa: BLE001
        logger.warning("KNOWLEDGE_FALLBACK index_load err=%s", type(exc).__name__)

    lg = last_good_index_path(path)
    if lg.exists():
        try:
            data = json.loads(lg.read_text(encoding="utf-8"))
            logger.warning("KNOWLEDGE_FALLBACK using_last_good_index")
            return [KnowledgeEntry.from_dict(x) for x in data.get("entries") or []]
        except Exception:
            pass
    entries, _ = load_all_human_entries()
    return entries


def maybe_rebuild_if_sources_changed() -> dict[str, Any] | None:
    """Optional startup helper: rebuild when human files are newer than index."""
    index = default_index_path()
    sources = [p for p in human_knowledge_paths() if p.exists()]
    if not sources:
        return None
    try:
        newest = max(p.stat().st_mtime for p in sources)
        if index.exists() and index.stat().st_mtime >= newest:
            return None
        return rebuild_index()
    except Exception as exc:  # noqa: BLE001
        logger.warning("KNOWLEDGE_FALLBACK mtime_rebuild err=%s", type(exc).__name__)
        return None
