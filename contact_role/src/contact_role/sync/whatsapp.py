"""Bridge to existing WhatsApp UI Playwright outbox (do not replace worker)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..roles import CanonicalRole


@dataclass
class WhatsAppEnqueueResult:
    code: str
    job_id: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "job_id": self.job_id,
            "message": self.message,
        }


def enqueue_whatsapp_ui_sync(
    phone: str | None,
    role: CanonicalRole | str,
    *,
    previous_role: CanonicalRole | str | None = None,
) -> WhatsAppEnqueueResult:
    """Fail-safe enqueue into existing whatsapp_ui_sync queue. Never raises."""
    try:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[4]
        sync_src = root / "whatsapp_ui_sync" / "src"
        if str(sync_src) not in sys.path:
            sys.path.insert(0, str(sync_src))
        from whatsapp_ui_sync.trigger import enqueue_role_sync_if_changed

        job = enqueue_role_sync_if_changed(
            phone,
            role,
            previous_role=previous_role,
        )
        if job is None:
            return WhatsAppEnqueueResult(
                code="SKIPPED",
                message="no whatsapp job (unknown/unchanged/disabled path)",
            )
        return WhatsAppEnqueueResult(
            code="ENQUEUED",
            job_id=getattr(job, "job_id", None),
            message="enqueued existing Playwright outbox",
        )
    except Exception as exc:
        return WhatsAppEnqueueResult(
            code="WHATSAPP_ENQUEUE_FAILED",
            message=str(exc)[:200],
        )
