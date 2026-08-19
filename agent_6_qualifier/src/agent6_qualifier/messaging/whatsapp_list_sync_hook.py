"""Fail-safe enqueue bridge for WhatsApp list sync (optional).

Does NOT change qualification, routing, Wazzup, or amoCRM.
Call after canonical role becomes known / changes; ignore return value.

On VPS the outbox is drained by whatsapp-ui-sync.timer against native Chrome CDP.
"""

from __future__ import annotations

from typing import Any


def enqueue_whatsapp_list_sync(
    phone: str | None,
    new_role: str | None,
    *,
    previous_role: str | None = None,
) -> Any:
    """Best-effort outbox enqueue. Never raises into caller."""
    try:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        sync_src = root / "whatsapp_ui_sync" / "src"
        if str(sync_src) not in sys.path:
            sys.path.insert(0, str(sync_src))
        from whatsapp_ui_sync.trigger import enqueue_role_sync_if_changed

        return enqueue_role_sync_if_changed(
            phone,
            new_role,
            previous_role=previous_role,
        )
    except Exception:
        return None
