#!/usr/bin/env python3
"""Controlled dual-sync dry-run diagnose.

Shows canonical priority + amoCRM plan + WhatsApp list plan.
NEVER writes amoCRM. NEVER changes WhatsApp lists.

Example:
  python3 scripts/contact_role_dual_sync_diagnose.py \\
    --phone +66625124001 --role CLIENT --source MANUAL
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "contact_role" / "src"))
sys.path.insert(0, str(ROOT / "whatsapp_ui_sync" / "src"))
sys.path.insert(0, str(ROOT / "agent_6_qualifier" / "src"))

for env_path in (ROOT / ".env", ROOT / "agent_6_qualifier" / ".env"):
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# Force safe defaults for this script regardless of env.
os.environ["CONTACT_ROLE_AMO_DRY_RUN"] = "true"
os.environ["CONTACT_ROLE_AMO_SYNC_ENABLED"] = "false"
os.environ["WHATSAPP_UI_DRY_RUN"] = "true"
os.environ.setdefault("WHATSAPP_UI_SYNC_ENABLED", "false")


def _load_amo_adapter() -> Any | None:
    """Optional read-only amo adapter if credentials exist. Never writes."""
    if not os.getenv("AMO_ACCESS_TOKEN") or not os.getenv("AMO_SUBDOMAIN"):
        return None
    try:
        from agent6_qualifier.amo import AmoClient

        client = AmoClient()

        class _Adapter:
            def find_contacts(self, query: str) -> list[dict]:
                return client.find_contacts(query)

            def get_contact(self, contact_id: int) -> dict:
                return client.get_contact(contact_id, with_entities="tags")

            def list_contact_custom_fields(self) -> list[dict]:
                return client.list_contact_custom_fields()

            def update_contact(self, contact_id: int, payload: dict) -> Any:
                raise RuntimeError("diagnose script forbids amoCRM writes")

        return _Adapter()
    except Exception as exc:
        print(f"(amoCRM adapter unavailable: {type(exc).__name__}: {exc})", file=sys.stderr)
        return None


def _probe_whatsapp_lists(phone: str, role: str) -> tuple[str, list[str], list[str]]:
    """Optional read-only CDP probe via existing worker dry-run path (no writes)."""
    notes: list[str] = []
    try:
        from whatsapp_ui_sync.config import WhatsAppUiSyncConfig
        from whatsapp_ui_sync.worker import WhatsAppNativeListSyncWorker

        cfg = WhatsAppUiSyncConfig.from_env()
        notes.append(
            f"config enabled={cfg.enabled} dry_run={cfg.dry_run} "
            "(script forces writes off)"
        )
        worker = WhatsAppNativeListSyncWorker(cfg)
        try:
            result = worker.sync_contact_role(
                phone, role, dry_run=True, confirm_write=False
            )
            code = getattr(result.code, "value", str(result.code))
            lists = list(result.current_lists or [])
            notes.append(f"worker dry-run code={code}")
            notes.append("confirm_write=false — no list writes")
            if code == "CONTACT_NOT_FOUND":
                return "NO", [], notes
            if code in {"DRY_RUN_PLAN", "ALREADY_SYNCED", "WHATSAPP_LIST_NOT_FOUND"}:
                return "YES", lists, notes
            if code in {"CONTACT_AMBIGUOUS"}:
                return "NO", lists, notes
            return "NOT_PROBED", lists, notes
        finally:
            try:
                worker.close()
            except Exception:
                pass
    except Exception as exc:
        notes.append(f"probe failed: {type(exc).__name__}: {exc}")
        return "NOT_PROBED", [], notes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Canonical role + dual sync DRY-RUN (no writes)"
    )
    parser.add_argument("--phone", required=True, help="E.164 phone, e.g. +66625124001")
    parser.add_argument(
        "--role",
        required=True,
        choices=["CLIENT", "OWNER", "AGENT", "UNKNOWN"],
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=[
            "MANUAL",
            "AGENT7_OUTREACH",
            "AGENT6_INBOUND",
            "INBOUND_LEAD",
            "WORKFLOW_CONTEXT",
            "MESSAGE_CLASSIFICATION",
            "EXISTING_CRM",
            "UNKNOWN",
        ],
    )
    parser.add_argument(
        "--probe-whatsapp",
        action="store_true",
        help="Optional read-only CDP probe for current lists (still no writes)",
    )
    parser.add_argument(
        "--no-amo",
        action="store_true",
        help="Skip amoCRM read lookup even if credentials exist",
    )
    args = parser.parse_args()

    from contact_role.diagnose import plan_dual_sync_dry_run
    from contact_role.state import ContactRoleStore

    store = ContactRoleStore()
    amo = None if args.no_amo else _load_amo_adapter()

    wa_found = "NOT_PROBED"
    wa_lists: list[str] = []
    wa_extra: list[str] = []
    if args.probe_whatsapp:
        wa_found, wa_lists, wa_extra = _probe_whatsapp_lists(args.phone, args.role)

    report = plan_dual_sync_dry_run(
        phone=args.phone,
        role=args.role,
        source=args.source,
        store=store,
        amo=amo,
        wa_current_lists=wa_lists,
        wa_contact_found=wa_found,
    )
    report.wa_notes.extend(wa_extra)
    # Hard safety assertion in output
    assert report.amo_would_write == "NO"
    assert report.wa_would_write == "NO"

    print(report.to_text())
    print()
    print("AMO_WRITE=NO  WHATSAPP_WRITE=NO  LIVE=DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
