#!/usr/bin/env python3
"""Manual Facebook login for Agent 7 persistent profile (owner communication)."""
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


def _wait_for_login(browser, timeout_sec: int = 900) -> None:
    if sys.stdin.isatty():
        input("\nНажмите Enter после успешного входа в Facebook...")
        return
    print("\nОжидание: войдите в Facebook и закройте окно браузера.")
    print(f"(таймаут {timeout_sec // 60} мин)")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        pages = list(getattr(browser, "pages", []) or [])
        if not pages:
            print("Окно браузера закрыто — сохраняем профиль.")
            return
        time.sleep(1)
    print("Таймаут ожидания — сохраняем профиль.")


def main() -> int:
    from agent7.profile_lock import facebook_profile_dir, facebook_profile_lock

    profile = facebook_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("Agent 7 — ручной вход в Facebook")
    print(f"Профиль браузера: {profile}")
    print("1. Сейчас откроется окно Chromium.")
    print("2. Войдите в Facebook (логин/пароль, 2FA если есть).")
    print("3. Убедитесь, что видите ленту / Marketplace.")
    print("4. Закройте окно браузера (или Enter в терминале).")
    print("=" * 60)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed")
        return 1

    with facebook_profile_lock(profile):
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile),
                headless=False,
                viewport={"width": 1280, "height": 900},
                locale="ru-RU",
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            page.bring_to_front()
            _wait_for_login(ctx)
            ctx.close()

    print(f"Готово. Cookies сохранены в {profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
