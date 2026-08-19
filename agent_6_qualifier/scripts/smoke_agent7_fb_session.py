#!/usr/bin/env python3
"""Smoke-check Agent 7/8 Facebook profile without sending messages."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent))

for env_path in (ROOT / ".env", Path("/opt/openhome/.env")):
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


def main() -> int:
    from playwright.sync_api import sync_playwright

    profile = facebook_profile_dir()
    if not profile.exists():
        print(f"PROFILE_MISSING: {profile}")
        return 2

    headless = os.getenv("AGENT7_FB_HEADLESS", "true").lower() in ("1", "true", "yes")

    with facebook_profile_lock(profile):
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile),
                headless=headless,
                viewport={"width": 1280, "height": 900},
                locale="ru-RU",
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(VERIFY_URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(2500)
            names = {c["name"] for c in ctx.cookies()}
            url = page.url
            ctx.close()

    has_c_user = "c_user" in names
    login_wall = "login" in url.lower() and "facebook.com" in url.lower()
    checkpoint = "checkpoint" in url.lower()

    print(f"profile: {profile}")
    print(f"has_c_user: {has_c_user}")
    print(f"url: {url[:120]}")
    print(f"login_wall: {login_wall}")
    print(f"checkpoint: {checkpoint}")
    print(f"auth_marker: {(profile / '.agent7_auth_ok').exists()}")

    if has_c_user and not login_wall and not checkpoint:
        print("STATUS: OK")
        return 0
    print("STATUS: LOGIN_REQUIRED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
