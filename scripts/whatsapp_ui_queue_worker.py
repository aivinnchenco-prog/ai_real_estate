#!/usr/bin/env python3
"""Process WhatsApp list-sync outbox jobs (one shot).

Intended for systemd timer on VPS. Never blocks Agent 6 qualification.

Requires:
  - native Chrome with CDP (scripts/whatsapp_ui_open_native_chrome.py)
  - WHATSAPP_UI_CDP_URL=http://127.0.0.1:9222
  - WHATSAPP_UI_SYNC_ENABLED=true and WHATSAPP_UI_DRY_RUN=false for real writes
"""

from __future__ import annotations

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
from whatsapp_ui_sync.trigger import process_queue_once


def main() -> int:
    cfg = WhatsAppUiSyncConfig.from_env()
    confirm = bool(cfg.enabled and not cfg.dry_run)
    print(
        json.dumps(
            {
                "enabled": cfg.enabled,
                "dry_run": cfg.dry_run,
                "confirm_write": confirm,
                "cdp_url": cfg.cdp_url,
                "queue_dir": str(cfg.queue_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not cfg.cdp_url:
        print(
            "WHATSAPP_UI_CDP_URL empty — start native Chrome first "
            "(whatsapp-ui-chrome.service / open_native_chrome.py)",
            file=sys.stderr,
        )
        return 2
    results = process_queue_once(config=cfg, confirm_write=confirm)
    print(json.dumps({"processed": len(results), "results": results}, ensure_ascii=False, indent=2))
    # Non-zero only on hard infra failure; per-job failures stay in queue/DONE/FAILED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
