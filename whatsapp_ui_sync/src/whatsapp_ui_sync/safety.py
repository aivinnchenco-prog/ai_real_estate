"""Human-safety guardrails for WhatsApp UI sync.

Only native list membership ASSIGN / REMOVE is allowed.
"""

from __future__ import annotations

FORBIDDEN_ACTIONS = frozenset(
    {
        "send_message",
        "delete_message",
        "archive_chat",
        "block_contact",
        "delete_contact",
        "create_broadcast",
        "start_call",
        "change_profile",
        "change_privacy",
        "create_list",
        "unlink_device",
        "logout",
        "manage_linked_devices",
    }
)

ALLOWED_WRITE_ACTIONS = frozenset(
    {
        "assign_list",
        "remove_list",
    }
)


def assert_action_allowed(action: str) -> None:
    name = (action or "").strip().lower()
    if name in FORBIDDEN_ACTIONS:
        raise PermissionError(f"WhatsApp UI sync forbids action: {action}")
    if name not in ALLOWED_WRITE_ACTIONS:
        raise PermissionError(f"WhatsApp UI sync unknown/disallowed action: {action}")


def expose_public_api() -> dict[str, tuple[str, ...]]:
    """Documented capability surface for tests."""
    return {
        "allowed_writes": tuple(sorted(ALLOWED_WRITE_ACTIONS)),
        "forbidden": tuple(sorted(FORBIDDEN_ACTIONS)),
    }
