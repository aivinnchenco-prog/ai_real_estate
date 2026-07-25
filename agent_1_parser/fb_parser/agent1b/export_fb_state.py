#!/usr/bin/env python3
"""Export Playwright storage state from local .fb_profile (run on Mac after login)."""

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent1b.fb_session import get_profile_path, has_saved_session
from playwright.async_api import async_playwright


async def main() -> None:
    out = _ROOT / "fb_storage_state.json"
    profile = get_profile_path()
    if not has_saved_session(profile):
        raise SystemExit("No FB session in profile — run login_fb.py first")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(profile),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(
            "https://www.facebook.com/marketplace/",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        await ctx.storage_state(path=str(out))
        await ctx.close()

    print(f"Exported: {out}")


if __name__ == "__main__":
    asyncio.run(main())
