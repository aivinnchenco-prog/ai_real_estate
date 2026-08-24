#!/usr/bin/env python3
"""Ручной вход в Facebook для публикации Groups + Marketplace (browser backend)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent))

from publisher_social.dotenv_util import load_dotenv

load_dotenv()

DEFAULT_PROFILE = Path.home() / "openhome" / "facebook_agent7"


def main() -> int:
    profile = Path(
        os.getenv("AGENT7_FACEBOOK_PROFILE_DIR", str(DEFAULT_PROFILE))
    ).expanduser().resolve()
    if profile.name != "facebook_agent7":
        print(
            f"ОШИБКА: профиль должен называться facebook_agent7, сейчас: {profile.name}",
            file=sys.stderr,
        )
        return 2

    lock_dir = ROOT / "data" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    os.environ["OPENHOME_FB_LOCK_DIR"] = str(lock_dir)
    os.environ["AGENT7_FACEBOOK_PROFILE_DIR"] = str(profile)
    profile.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Facebook — вход для публикации (Groups + Marketplace)")
    print(f"Профиль: {profile}")
    print()
    print("1. Сейчас откроется окно Chromium.")
    print("2. Войдите в нужный Facebook-аккаунт (2FA если спросит).")
    print("3. Убедитесь, что видите ленту / Marketplace.")
    print("4. Закройте окно браузера — сессия сохранится.")
    print("=" * 60)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Установите: pip install playwright && playwright install chromium", file=sys.stderr)
        return 1

    from publisher_social.browser.profile import facebook_profile_lock

    with facebook_profile_lock(profile):
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile),
                headless=False,
                viewport={"width": 1280, "height": 900},
                locale="ru-RU",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=90000)
            page.bring_to_front()

            if sys.stdin.isatty():
                input("\nНажмите Enter после входа и закрытия браузера (или просто закройте окно)...")
            else:
                print("\nОжидание: войдите и закройте окно браузера...")
                deadline = time.time() + 1800
                while time.time() < deadline:
                    if not ctx.pages:
                        break
                    time.sleep(1)

            ctx.close()

    print(f"\nГотово. Сессия сохранена в {profile}")
    print("Проверка: PYTHONPATH=src:.. python3 scripts/smoke_fb_browser_session.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
