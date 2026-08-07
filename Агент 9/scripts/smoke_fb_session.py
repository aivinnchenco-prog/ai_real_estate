#!/usr/bin/env python3
"""Smoke-test: open Facebook with saved Agent 9 profile (no messages sent)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from agent9_connector.connectors.facebook import FacebookConnector
from agent9_connector.connectors import facebook_playwright as pw


def main() -> int:
    fb = FacebookConnector(mock_mode=False, headless=False)
    print(f"Profile: {fb.profile_path()}")
    fb.ensure_browser()
    page = fb._page
    page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    state = pw.detect_security_state(page.url, page.inner_text("body")[:5000])
    print("security:", state or "OK")
    print("url:", page.url)
    if state:
        print("Нужен повторный login: python3 scripts/facebook_login.py")
        return 1
    print("Сессия Facebook активна. Закройте окно браузера.")
    input("Enter...")
    fb.stop_browser()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
