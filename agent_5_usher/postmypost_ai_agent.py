"""Agent 5 Usher — PostMyPost AI agent integration boundary."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_PENDING = "pending_postmypost_ai_agent"
STATUS_BLOCKED = "blocked_postmypost_ai_agent_not_configured"
STATUS_QUEUED = "queued_postmypost_ai_agent"
STATUS_STARTED = "started_postmypost_ai_agent"
STATUS_FAILED = "failed_postmypost_ai_agent"
STATUS_DONE = "done_postmypost_ai_agent"

NOT_CONFIGURED_WARNING = "PostMyPost AI Agent integration is not configured"

# PostMyPost REST client (postmypost_client.py) covers publications only.
# setup_postmypost_funnel.py documents that automations are configured in UI.
MISSING_INTEGRATION_CONTRACT = {
    "endpoint": "unknown — no PostMyPost automation/AI agent API in repo",
    "method": "unknown",
    "auth_header": "Bearer POSTMYPOST_API_TOKEN (publications only; agent trigger TBD)",
    "required_request_payload": [
        "object_id / listing_id",
        "publication_id",
        "post_url",
        "platform",
        "idempotency_key",
    ],
    "required_response_fields": [
        "external agent/job id",
        "status",
    ],
    "publication_linking": "how to bind a publication to an automation/AI assistant run",
    "status_polling": "webhook URL or polling endpoint for agent run status",
    "reference_docs": [
        "agent_4_publisher/scripts/setup_postmypost_funnel.py",
        "agent_4_publisher/scripts/export_postmypost_reply_prompt.py",
        "agent_4_publisher/scripts/post_reply_agent.py",
    ],
}

_STATE_FILE = Path(__file__).resolve().parent / "data" / "postmypost_ai_agent_state.json"


def _state_path() -> Path:
    override = (os.getenv("AGENT5_STATE_PATH") or "").strip()
    path = Path(override).expanduser() if override else _STATE_FILE
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


def integration_ready() -> bool:
    """True when Agent 5 can call a real PostMyPost AI automation API."""
    return False


def missing_integration_contract() -> dict[str, Any]:
    """Document what is required before a network integration can be implemented."""
    return dict(MISSING_INTEGRATION_CONTRACT)


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

    No network call is made until PostMyPost documents an automation/AI agent API.
    """
    _ = config
    key = _run_key(page_id, publication_id)
    state = load_state()
    runs = state.setdefault("runs", {})
    terminal = {
        STATUS_PENDING,
        STATUS_BLOCKED,
        STATUS_QUEUED,
        STATUS_STARTED,
        STATUS_DONE,
    }
    if key in runs and runs[key].get("status") in terminal:
        return {
            "ok": True,
            "skipped": True,
            "status": runs[key]["status"],
            "warning": runs[key].get("warning"),
            "reason": "already_queued",
        }

    entry = {
        "status": STATUS_BLOCKED if not integration_ready() else STATUS_PENDING,
        "page_id": page_id,
        "object_id": object_id,
        "publication_id": publication_id,
        "post_url": post_url,
        "platform": platform,
        "idempotency_key": key,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "warning": NOT_CONFIGURED_WARNING,
        "missing_contract": missing_integration_contract(),
    }
    runs[key] = entry
    save_state(state)
    return {
        "ok": True,
        "skipped": False,
        "status": entry["status"],
        "warning": NOT_CONFIGURED_WARNING,
        "run_key": key,
    }
