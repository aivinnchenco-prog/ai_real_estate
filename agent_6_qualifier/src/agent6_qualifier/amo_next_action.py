"""Map amo stage + open tasks → operational next action."""

from __future__ import annotations

from .amo_tasks_config import parse_task_key

PRIORITY = (
    "need_human",
    "owner_followup",
    "conditions_approval",
    "client_followup",
    "booking_next_step",
)


def open_task_keys(tasks: list[dict]) -> set[str]:
    keys: set[str] = set()
    for task in tasks:
        key = parse_task_key(task.get("text") or "")
        if key:
            keys.add(key)
    return keys


def resolve_next_action(
    stage: str,
    open_tasks: list[dict],
    session=None,
) -> str:
    """Canonical operational next action for dashboards/handlers."""
    keys = open_task_keys(open_tasks)
    for key in PRIORITY:
        if key in keys:
            return key

    if stage in ("Новый лид", "Квалификация"):
        return "qualify_client"
    if stage == "Подбор":
        return "client_followup"
    if stage == "Запрос владельцу":
        return "owner_followup"
    if stage == "Согласование условий":
        return "conditions_approval"
    if stage == "Бронь подтверждена":
        return "booking_next_step"
    return "qualify_client"
