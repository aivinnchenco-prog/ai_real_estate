#!/usr/bin/env python3
"""Facebook login on headless VPS via Chrome DevTools (SSH tunnel + chrome://inspect)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for env_path in (ROOT / ".env", Path("/opt/openhome/.env"), Path("/opt/openhome/app/.env")):
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from agent9_connector.config_loader import facebook_profile_dir
from agent9_connector.profile_lock import facebook_profile_lock

VERIFY_URL = os.environ.get(
    "AGENT9_FB_VERIFY_URL",
    "https://www.facebook.com/marketplace/item/1515735863510647",
)
DEBUG_PORT = int(os.environ.get("AGENT9_FB_DEBUG_PORT", "9222"))
TIMEOUT_SEC = int(os.environ.get("AGENT9_FB_LOGIN_TIMEOUT_SEC", "1800"))


def _logged_in(cookies: list) -> bool:
    return "c_user" in {c.get("name") for c in cookies}


def main() -> int:
    from playwright.sync_api import sync_playwright

    profile = facebook_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Agent 9 — Facebook login на VPS (remote debugging)")
    print(f"Профиль: {profile}")
    print(f"Debug port: {DEBUG_PORT} (только 127.0.0.1 на VPS)")
    print()
    print("1) На Mac откройте терминал:")
    print(f"   ssh -L {DEBUG_PORT}:127.0.0.1:{DEBUG_PORT} USER@VPS")
    print("2) Chrome → chrome://inspect → Configure → localhost:{port}".format(port=DEBUG_PORT))
    print("3) Нажмите inspect у вкладки Facebook, войдите (2FA если нужно)")
    print("4) Откройте Marketplace — скрипт сам завершится при успехе")
    print("=" * 70)

    with facebook_profile_lock(profile):
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile),
                headless=True,
                args=[
                    f"--remote-debugging-port={DEBUG_PORT}",
                    "--remote-debugging-address=127.0.0.1",
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
                viewport={"width": 1280, "height": 900},
                locale="ru-RU",
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=90000)

            deadline = time.time() + TIMEOUT_SEC
            ok = False
            while time.time() < deadline:
                cookies = ctx.cookies()
                if _logged_in(cookies):
                    try:
                        page.goto(VERIFY_URL, wait_until="domcontentloaded", timeout=90000)
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass
                    if "login" not in page.url.lower():
                        ok = True
                        print("SUCCESS: c_user present and listing page reachable")
                        break
                remaining = int(deadline - time.time())
                print(f"waiting for login... ({remaining}s left, c_user={_logged_in(cookies)})")
                time.sleep(5)

            ctx.close()

    if not ok:
        print("TIMEOUT: login not completed")
        return 1
    print(f"Cookies saved in {profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
