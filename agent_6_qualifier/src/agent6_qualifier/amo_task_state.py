"""Durable markers for task escalation dedup (no new DB)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .runtime_paths import amo_task_state_path

_STATE_PATH = amo_task_state_path()


def _load() -> dict:
    if not _STATE_PATH.exists():
        return {"escalated": {}, "client_followups_sent": {}}
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"escalated": {}, "client_followups_sent": {}}
    data.setdefault("escalated", {})
    data.setdefault("client_followups_sent", {})
    return data


def _save(data: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def escalation_key(lead_id: int, task_key: str) -> str:
    return f"{lead_id}:{task_key}"


def was_escalated(lead_id: int, task_key: str) -> bool:
    return escalation_key(lead_id, task_key) in (_load().get("escalated") or {})


def mark_escalated(lead_id: int, task_key: str) -> None:
    data = _load()
    data["escalated"][escalation_key(lead_id, task_key)] = time.time()
    _save(data)


def client_auto_followups_sent(lead_id: int) -> int:
    return int((_load().get("client_followups_sent") or {}).get(str(lead_id), 0))


def increment_client_auto_followups(lead_id: int) -> int:
    data = _load()
    key = str(lead_id)
    n = int(data["client_followups_sent"].get(key, 0)) + 1
    data["client_followups_sent"][key] = n
    _save(data)
    return n
