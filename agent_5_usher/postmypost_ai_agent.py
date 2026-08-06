"""Agent 5 Usher — PostMyPost AI agent integration boundary (no external API yet)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_PENDING = "pending_postmypost_ai_agent"
STATUS_QUEUED = "queued_postmypost_ai_agent"
STATUS_DONE = "done_postmypost_ai_agent"

_STATE_FILE = Path(__file__).resolve().parent / "data" / "postmypost_ai_agent_state.json"


def _state_path() -> Path:
    path = _STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"runs": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"runs": {}}


def save_state(state: dict[str, Any]) -> None:
    _state_path().write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _run_key(page_id: str, publication_id: str | None) -> str:
    return f"{page_id}:{publication_id or 'unknown'}"


def get_run_status(page_id: str, publication_id: str | None = None) -> str | None:
    state = load_state()
    entry = state.get("runs", {}).get(_run_key(page_id, publication_id))
    return entry.get("status") if entry else None


def queue_postmypost_ai_agent(
    *,
    page_id: str,
    object_id: str,
    publication_id: str | None = None,
    post_url: str | None = None,
    platform: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Record intent to run PostMyPost AI agent after a successful publication.

    Network execution is not implemented — status remains pending until Agent 5
    gains a real PostMyPost automation trigger.
    """
    _ = config
    key = _run_key(page_id, publication_id)
    state = load_state()
    runs = state.setdefault("runs", {})
    if key in runs and runs[key].get("status") in {
        STATUS_PENDING,
        STATUS_QUEUED,
        STATUS_DONE,
    }:
        return {"ok": True, "skipped": True, "status": runs[key]["status"], "reason": "already_queued"}

    runs[key] = {
        "status": STATUS_PENDING,
        "page_id": page_id,
        "object_id": object_id,
        "publication_id": publication_id,
        "post_url": post_url,
        "platform": platform,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "note": "PostMyPost AI agent trigger not implemented; awaiting Agent 5 service",
    }
    save_state(state)
    return {"ok": True, "skipped": False, "status": STATUS_PENDING, "run_key": key}


def integration_ready() -> bool:
    """True when Agent 5 can call a real PostMyPost AI automation API."""
    return False
