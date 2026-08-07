"""Config for amoCRM operational tasks (not asyncio.create_task)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "amo_tasks.json"

TASK_KEYS = frozenset({
    "owner_followup",
    "client_followup",
    "need_human",
    "conditions_approval",
    "booking_next_step",
})

REQUIRED_TASK_KEYS = frozenset({"owner_followup", "client_followup", "need_human"})


@lru_cache(maxsize=1)
def load_amo_tasks_config() -> dict:
    data: dict = {}
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    return data


def amo_tasks_enabled() -> bool:
    env = os.getenv("AMO_TASKS_ENABLED", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    section = load_amo_tasks_config().get("amo_tasks") or {}
    return bool(section.get("enabled", True))


def task_prefix() -> str:
    section = load_amo_tasks_config().get("amo_tasks") or {}
    return str(section.get("task_prefix") or "OPENHOME").strip().upper()


def owner_followup_hours() -> int:
    section = load_amo_tasks_config().get("amo_tasks") or {}
    return int(os.getenv("AMO_OWNER_FOLLOWUP_HOURS") or section.get("owner_followup_hours", 2))


def client_followup_hours() -> int:
    section = load_amo_tasks_config().get("amo_tasks") or {}
    return int(os.getenv("AMO_CLIENT_FOLLOWUP_HOURS") or section.get("client_followup_hours", 24))


def need_human_minutes() -> int:
    section = load_amo_tasks_config().get("amo_tasks") or {}
    return int(os.getenv("AMO_NEED_HUMAN_MINUTES") or section.get("need_human_minutes", 15))


def max_automatic_client_followups() -> int:
    section = load_amo_tasks_config().get("amo_tasks") or {}
    nested = section.get("client_followup") or {}
    if isinstance(nested, dict) and "max_automatic_followups" in nested:
        return int(nested["max_automatic_followups"])
    return int(section.get("max_automatic_client_followups", 1))


def default_task_type_id() -> int:
    types = load_amo_tasks_config().get("task_type_ids") or {}
    return int(os.getenv("AMO_TASK_TYPE_ID") or types.get("default", 1))


def default_responsible_user_id() -> int | None:
    raw = (os.getenv("AMO_DEFAULT_RESPONSIBLE_USER_ID") or "").strip()
    return int(raw) if raw.isdigit() else None


def manager_responsible_user_id() -> int | None:
    raw = (os.getenv("AMO_MANAGER_RESPONSIBLE_USER_ID") or "").strip()
    if raw.isdigit():
        return int(raw)
    return default_responsible_user_id()


def format_task_text(task_key: str, body: str) -> str:
    prefix = task_prefix()
    header = f"[{prefix}:{task_key}]"
    body = (body or "").strip()
    return f"{header}\n{body}" if body else header


def parse_task_key(text: str) -> str | None:
    if not text:
        return None
    prefix = task_prefix()
    marker = f"[{prefix}:"
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(marker):
            continue
        rest = line[len(marker) :]
        if rest.endswith("]"):
            key = rest[:-1]
            if key in TASK_KEYS:
                return key
    return None
