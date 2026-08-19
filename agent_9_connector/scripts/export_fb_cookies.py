#!/usr/bin/env python3
"""Export Playwright storage state from a working local FB profile (run on Mac after login)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for env_path in (ROOT / ".env", Path("/opt/openhome/.env")):
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from agent9_connector.config_loader import facebook_profile_dir, legacy_facebook_profile_dir


async def main() -> int:
    src_profile = os.environ.get("AGENT9_FB_EXPORT_PROFILE", "").strip()
    candidates = []
    if src_profile:
        candidates.append(Path(src_profile))
    candidates.extend([
        ROOT.parent / "agent_1_parser" / "fb_parser" / ".fb_profile",
        facebook_profile_dir(),
        legacy_facebook_profile_dir(),
    ])
    profile = next((p for p in candidates if p.exists()), None)
    if not profile:
        raise SystemExit("No FB profile found for export")

    out = Path(os.environ.get("AGENT9_FB_EXPORT_OUT", ROOT / "data" / "fb_storage_state_export.json"))
    out.parent.mkdir(parents=True, exist_ok=True)

    verify_url = os.environ.get(
        "AGENT9_FB_VERIFY_URL",
        "https://www.facebook.com/marketplace/item/1515735863510647",
    )

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(profile),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(verify_url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2000)
        if "login" in page.url.lower():
            raise SystemExit(f"Profile not logged in: {page.url}")
        await ctx.storage_state(path=str(out))
        names = {c["name"] for c in await ctx.cookies()}
        await ctx.close()

    if "c_user" not in names:
        raise SystemExit("Export missing c_user cookie")
    print(f"Exported {len(names)} cookie names to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
