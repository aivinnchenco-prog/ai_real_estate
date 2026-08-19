"""Shared Agent6 session extraction — Telegram and WhatsApp must call this.

Archive TG contract (source of truth):
  brain.extract_lead_update(message, session.lead, context=..., history=...)

WhatsApp previously broke parity by passing session + knowledge= and swallowing
errors → empty updates → repeated «дату заезда».
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from agent6_qualifier.qualifier import Session

logger = logging.getLogger(__name__)


def extract_lead_update_for_session(
    message: str,
    session: Session,
    *,
    notify_error: Any | None = None,
    use_llm: bool = True,
) -> dict:
    """Extract + merge deterministic hints (LLM keys win; hints fill gaps only)."""
    from agent6_qualifier import brain
    from agent6_qualifier.context import build_knowledge, format_history
    from agent6_qualifier.qualification_hints import (
        merge_hints,
        qualification_hints_from_text,
    )

    update: dict = {}
    if use_llm:
        try:
            update = (
                brain.extract_lead_update(
                    message,
                    session.lead,
                    context=build_knowledge(session),
                    history=format_history(session.history),
                )
                or {}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agent6_extract_failed chat=%s err=%s",
                getattr(session, "chat_id", ""),
                type(exc).__name__,
            )
            if notify_error is not None:
                try:
                    notify_error(
                        "gemini.extract",
                        str(exc),
                        "поля из сообщения не извлечены, диалог продолжен по шаблонам",
                    )
                except Exception:  # noqa: BLE001
                    pass
            update = {}

    hints = qualification_hints_from_text(message, today=date.today())
    merged = merge_hints(update, hints)
    logger.info(
        "agent6_extract chat=%s extracted=%s hints=%s merged=%s",
        getattr(session, "chat_id", ""),
        sorted(update.keys()),
        sorted(hints.keys()),
        sorted(merged.keys()),
    )
    return merged
