#!/usr/bin/env python3
"""Manual Facebook login for Agent 9 persistent profile."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_ENV = ROOT / ".env"
if _ENV.exists():
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from agent9_connector.connectors.facebook import FacebookConnector


def _wait_for_login(fb: FacebookConnector, timeout_sec: int = 900) -> None:
    """Wait until user closes browser or presses Enter in interactive terminal."""
    if sys.stdin.isatty():
        input("\nНажмите Enter после успешного входа в Facebook...")
        return
    print("\nОжидание: войдите в Facebook и закройте окно браузера.")
    print(f"(таймаут {timeout_sec // 60} мин)")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        pages = list(getattr(fb._browser, "pages", []) or [])
        if not pages:
            print("Окно браузера закрыто — сохраняем профиль.")
            return
        time.sleep(1)
    print("Таймаут ожидания — сохраняем профиль.")


def main() -> int:
    fb = FacebookConnector(headless=False)
    profile = fb.profile_path()
    print("=" * 60)
    print("Agent 9 — ручной вход в Facebook")
    print(f"Профиль браузера: {profile}")
    print("1. Сейчас откроется окно Chromium.")
    print("2. Войдите в Facebook (логин/пароль, 2FA если есть).")
    print("3. Убедитесь, что видите ленту / Marketplace.")
    print("4. Закройте окно браузера (или Enter в терминале).")
    print("=" * 60)
    fb.start_browser()
    try:
        if fb._page:
            fb._page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            fb._page.bring_to_front()
        _wait_for_login(fb)
    finally:
        fb.stop_browser()
    print(f"Готово. Cookies сохранены в {profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
