"""Read-only diagnostics for WhatsApp UI sync."""

from __future__ import annotations

from typing import Any

from .config import WhatsAppUiSyncConfig
from .worker import WhatsAppNativeListSyncWorker


def diagnose(config: WhatsAppUiSyncConfig | None = None) -> dict[str, Any]:
    """Probe browser/auth/lists without mutating membership or Wazzup."""
    cfg = config or WhatsAppUiSyncConfig.from_env()
    worker = WhatsAppNativeListSyncWorker(cfg)
    report: dict[str, Any] = {
        "browser": "DISCONNECTED",
        "whatsapp_web": "FAILED",
        "lists": {
            cfg.owner_list_name: "NOT_CHECKED",
            cfg.client_list_name: "NOT_CHECKED",
        },
        "wazzup": "UNCHANGED",
        "sync_enabled": cfg.enabled,
        "dry_run": cfg.dry_run,
        "profile_dir": str(cfg.profile_dir),
        "queue_dir": str(cfg.queue_dir),
        "role_mapping": {
            "CLIENT": cfg.client_list_name,
            "OWNER": cfg.owner_list_name,
            "AGENT": cfg.agent_list_name,
            "UNKNOWN": None,
        },
        "message": "",
    }
    try:
        health = worker.healthcheck()
        report["browser"] = health.get("browser", "FAILED")
        wa = health.get("whatsapp_web", "FAILED")
        if wa == "AUTH_REQUIRED":
            report["whatsapp_web"] = "FAILED"
            report["browser"] = "AUTH_REQUIRED"
        else:
            report["whatsapp_web"] = wa if wa in {"READY", "FAILED"} else "FAILED"
            if wa == "AUTH_REQUIRED":
                report["browser"] = "AUTH_REQUIRED"
        if health.get("auth") == "AUTH_REQUIRED":
            report["browser"] = "AUTH_REQUIRED"
            report["whatsapp_web"] = "FAILED"
        report["message"] = health.get("message", "")

        # List presence check only when READY
        if health.get("auth") == "READY" or health.get("whatsapp_web") == "READY":
            page = worker.session.page
            if page is not None:
                for name in (cfg.owner_list_name, cfg.client_list_name):
                    found = False
                    try:
                        # Soft probe: exact visible text somewhere (no clicks that mutate)
                        loc = page.get_by_text(name, exact=True)
                        found = loc.count() > 0
                    except Exception:
                        found = False
                    report["lists"][name] = "FOUND" if found else "NOT FOUND"
                # Note: lists may only appear inside contact panel — NOT FOUND is OK
                # in diagnose without opening a contact.
                report["lists_note"] = (
                    "List visibility on main shell is best-effort; "
                    "per-contact panel is authoritative during sync."
                )
    except Exception as exc:
        report["message"] = str(exc)
        report["browser"] = "FAILED"
        report["whatsapp_web"] = "FAILED"
    finally:
        try:
            worker.close()
        except Exception:
            pass
    return report


def format_diagnose_report(report: dict[str, Any]) -> str:
    lines = [
        "WhatsApp UI Sync — diagnostic (no changes)",
        "=" * 50,
        f"Browser:        {report.get('browser')}",
        f"WhatsApp Web:   {report.get('whatsapp_web')}",
        "Lists:",
    ]
    for name, status in (report.get("lists") or {}).items():
        lines.append(f"  {name}: {status}")
    lines.extend(
        [
            f"Wazzup:         {report.get('wazzup')}",
            f"Sync enabled:   {report.get('sync_enabled')}",
            f"Dry run:        {report.get('dry_run')}",
            f"Profile:        {report.get('profile_dir')}",
            f"Queue:          {report.get('queue_dir')}",
            f"Message:        {report.get('message')}",
        ]
    )
    if report.get("lists_note"):
        lines.append(f"Note:           {report.get('lists_note')}")
    return "\n".join(lines)
