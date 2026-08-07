"""Startup validation for amoCRM SLA worker (not Agent 6 message runtime)."""

from __future__ import annotations

import os

from .amo_tasks_config import amo_tasks_enabled, default_task_type_id


def validate_worker_startup() -> list[str]:
    """Return human-readable config errors. Empty list => OK to run worker."""
    if not amo_tasks_enabled():
        return []

    errors: list[str] = []
    if not (os.getenv("AMO_ACCESS_TOKEN") or "").strip():
        errors.append("AMO_ACCESS_TOKEN not set")
    if not (os.getenv("AMO_SUBDOMAIN") or "").strip():
        errors.append("AMO_SUBDOMAIN not set")

    task_type_env = (os.getenv("AMO_TASK_TYPE_ID") or "").strip()
    if task_type_env:
        if not task_type_env.isdigit() or int(task_type_env) <= 0:
            errors.append(f"AMO_TASK_TYPE_ID invalid: {task_type_env!r}")
    else:
        try:
            tid = default_task_type_id()
            if tid <= 0:
                errors.append("task_type_id fallback invalid (must be > 0)")
        except (TypeError, ValueError):
            errors.append("task_type_id fallback invalid (config/AMO_TASK_TYPE_ID)")

    for env_name in ("AMO_DEFAULT_RESPONSIBLE_USER_ID", "AMO_MANAGER_RESPONSIBLE_USER_ID"):
        raw = (os.getenv(env_name) or "").strip()
        if raw and not raw.isdigit():
            errors.append(f"{env_name} invalid: {raw!r}")

    return errors


def worker_startup_ok() -> bool:
    return not validate_worker_startup()
