#!/usr/bin/env python3
"""Controlled WhatsApp native-list sync for one contact.

Default: dry-run (no checkbox/Save clicks).
Real write requires:
  --confirm-write
  WHATSAPP_UI_SYNC_ENABLED=true
  WHATSAPP_UI_DRY_RUN=false
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "whatsapp_ui_sync" / "src"))

for env_path in (ROOT / ".env", ROOT / "agent_6_qualifier" / ".env"):
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from whatsapp_ui_sync.config import WhatsAppUiSyncConfig
from whatsapp_ui_sync.roles import CanonicalRole
from whatsapp_ui_sync.worker import WhatsAppNativeListSyncWorker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone", required=True, help="E.164 phone, e.g. +66625124002")
    parser.add_argument(
        "--role",
        required=True,
        choices=[r.value for r in CanonicalRole],
        help="Canonical role",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Plan only (default: true)",
    )
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="Allow real list membership changes (still needs env flags)",
    )
    args = parser.parse_args()

    base = WhatsAppUiSyncConfig.from_env()
    cfg = WhatsAppUiSyncConfig(
        enabled=base.enabled,
        dry_run=True if args.dry_run else False,
        headless=base.headless,
        profile_dir=base.profile_dir,
        queue_dir=base.queue_dir,
        client_list_name=base.client_list_name,
        owner_list_name=base.owner_list_name,
        agent_list_name=base.agent_list_name,
        action_delay_ms=base.action_delay_ms,
        max_attempts=base.max_attempts,
        page_load_timeout_ms=base.page_load_timeout_ms,
        ready_extra_wait_ms=base.ready_extra_wait_ms,
        browser_channel=base.browser_channel,
        locale=base.locale,
        cdp_url=base.cdp_url or (os.getenv("WHATSAPP_UI_CDP_URL") or "").strip(),
    )

    worker = WhatsAppNativeListSyncWorker(cfg)
    try:
        result = worker.sync_contact_role(
            args.phone,
            args.role,
            confirm_write=args.confirm_write,
            dry_run=args.dry_run,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        print("\nSummary:")
        print(f"  phone:          {result.phone}")
        print(f"  canonical role: {result.canonical_role}")
        print(f"  current lists:  {result.current_lists}")
        print(f"  target list:    {result.target_list}")
        print(f"  would add:      {result.would_add}")
        print(f"  would remove:   {result.would_remove}")
        print(f"  code:           {result.code.value}")
        print(f"  dry_run:        {result.dry_run}")
        return 0 if result.ok else 1
    finally:
        worker.close()


if __name__ == "__main__":
    raise SystemExit(main())
