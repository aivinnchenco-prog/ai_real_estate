#!/usr/bin/env python3
"""Read-only WhatsApp UI sync diagnostic. No membership changes. Wazzup untouched."""

from __future__ import annotations

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

# Prefer CDP to the native Chrome daemon (VPS / local)
os.environ.setdefault("WHATSAPP_UI_CDP_URL", "http://127.0.0.1:9222")

from whatsapp_ui_sync.diagnose import diagnose, format_diagnose_report


def main() -> int:
    report = diagnose()
    print(format_diagnose_report(report))
    print(f"CDP URL:       {os.getenv('WHATSAPP_UI_CDP_URL')}")
    if report.get("browser") == "AUTH_REQUIRED" or report.get("whatsapp_web") != "READY":
        print("\nIf AUTH required: python3 scripts/whatsapp_ui_login.py")
        print("If Chrome down:   systemctl status whatsapp-ui-chrome.service")
        print("                  OR python3 scripts/whatsapp_ui_open_native_chrome.py")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
