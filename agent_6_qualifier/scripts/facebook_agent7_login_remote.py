#!/usr/bin/env python3
"""Facebook login for Agent 7/8 on headless VPS (Chrome DevTools via SSH tunnel)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUALIFIER_SRC = ROOT.parent / "agent_6_qualifier" / "src"
sys.path.insert(0, str(QUALIFIER_SRC))
sys.path.insert(0, str(ROOT.parent))

for env_path in (
    ROOT / ".env",
    ROOT.parent / "agent_6_qualifier" / ".env",
    Path("/opt/openhome/.env"),
):
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from agent7.profile_lock import facebook_profile_dir, facebook_profile_lock

VERIFY_URL = os.environ.get(
    "AGENT7_FB_VERIFY_URL",
    "https://www.facebook.com/marketplace/",
)
DEBUG_PORT = int(os.environ.get("AGENT7_FB_DEBUG_PORT", "9223"))
TIMEOUT_SEC = int(os.environ.get("AGENT7_FB_LOGIN_TIMEOUT_SEC", "1800"))


def _logged_in(cookies: list) -> bool:
    return "c_user" in {c.get("name") for c in cookies}


def main() -> int:
    from playwright.sync_api import sync_playwright

    profile = facebook_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Agent 7/8 — Facebook login на VPS (remote debugging)")
    print(f"Профиль (изолирован от Agent1 / Availability): {profile}")
    print(f"Debug port: {DEBUG_PORT} (127.0.0.1 на VPS)")
    print()
    print("1) На Mac:")
    print(f"   ssh -L {DEBUG_PORT}:127.0.0.1:{DEBUG_PORT} USER@VPS")
    print("2) Chrome → chrome://inspect → Configure → localhost:{port}".format(port=DEBUG_PORT))
    print("3) Inspect вкладки Facebook, войдите (2FA если нужно)")
    print("4) Откройте Marketplace — скрипт завершится при успехе")
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
                    url = page.url.lower()
                    if "login" not in url or "checkpoint" not in url:
                        ok = True
                        break
                time.sleep(2)

            ctx.close()

    if ok:
        marker = profile / ".agent7_auth_ok"
        marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), encoding="utf-8")
        print(f"OK — сессия сохранена в {profile}")
        return 0

    print("Таймаут или login не завершён — профиль может быть не авторизован.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
