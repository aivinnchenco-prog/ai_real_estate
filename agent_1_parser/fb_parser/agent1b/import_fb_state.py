#!/usr/bin/env python3
"""Import fb_storage_state.json into Linux .fb_profile (cookies portable across OS)."""

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent1b.fb_session import get_profile_path, has_saved_session, is_logged_in
from playwright.async_api import async_playwright


async def main() -> None:
    src = _ROOT / "fb_storage_state.json"
    if not src.exists():
        raise SystemExit(f"Missing {src} — export on Mac: python agent1b/export_fb_state.py")

    profile = get_profile_path()
    state = json.loads(src.read_text(encoding="utf-8"))
    cookies = state.get("cookies") or []
    if not cookies:
        raise SystemExit("No cookies in storage state")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(profile),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await ctx.add_cookies(cookies)
        await page.goto(
            "https://www.facebook.com/marketplace/",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        if not await is_logged_in(page):
            raise SystemExit("Import failed — still not logged in on server")
        await ctx.close()

    if not has_saved_session(profile):
        raise SystemExit("Import failed — c_user cookie not saved")
    print(f"OK: session in {profile}")


if __name__ == "__main__":
    asyncio.run(main())
