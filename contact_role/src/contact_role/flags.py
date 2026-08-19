"""Feature flags for canonical role auto-assign + dual sync mirrors.

Independent kill switches:
  CONTACT_ROLE_AUTO_ASSIGN_ENABLED — event-driven role assign (no backfill)
  CONTACT_ROLE_AMO_SYNC_ENABLED — amoCRM mirror writes
  WHATSAPP_UI_SYNC_ENABLED — WhatsApp native list mirror (existing)
  CONTACT_ROLE_ONE_SHOT_LIVE_TEST — arm first real Agent7 OWNER/AGENT event only
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def auto_assign_enabled() -> bool:
    """Gate for Agent 6/7 event-driven role assignment. Default OFF (safe)."""
    return _env_bool("CONTACT_ROLE_AUTO_ASSIGN_ENABLED", False)


def one_shot_live_test_enabled() -> bool:
    """Allow at most one new Agent7 OWNER/AGENT live event after arming.

    Default OFF. Requires CONTACT_ROLE_AUTO_ASSIGN_ENABLED=true as well.
    Consumed state is persisted to disk (env is not rewritten at runtime).
    """
    return _env_bool("CONTACT_ROLE_ONE_SHOT_LIVE_TEST", False)


def amo_sync_enabled() -> bool:
    return _env_bool("CONTACT_ROLE_AMO_SYNC_ENABLED", False)


def amo_dry_run() -> bool:
    # Default true until explicitly disabled for live mirror writes.
    return _env_bool("CONTACT_ROLE_AMO_DRY_RUN", True)


def whatsapp_ui_sync_enabled() -> bool:
    return _env_bool("WHATSAPP_UI_SYNC_ENABLED", False)


def whatsapp_ui_dry_run() -> bool:
    return _env_bool("WHATSAPP_UI_DRY_RUN", True)


def amo_process_async() -> bool:
    """Process amo outbox job in a daemon thread (never blocks outreach)."""
    return _env_bool("CONTACT_ROLE_AMO_PROCESS_ASYNC", True)
