#!/usr/bin/env python3
"""Login to Facebook and save session into persistent browser profile."""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent1b.fb_session import (
    ensure_facebook_session,
    get_profile_path,
    has_saved_session,
    human_delay,
)


async def main() -> None:
    load_dotenv(_ROOT / ".env")
    profile = get_profile_path()

    email = os.getenv("FB_EMAIL", "").strip()
    if email:
        print(f"Using FB_EMAIL from .env ({email[:3]}***)")
    else:
        print("FB_EMAIL not set — only manual login in browser will work.")

    print(f"Profile: {profile}")
    print("Ensuring Facebook session (browser may open for manual/2FA login)...")
    await ensure_facebook_session(for_login=True)
    await human_delay()
    if not has_saved_session(profile):
        raise SystemExit("Login finished but c_user cookie missing — retry login_fb.py")
    print("Done. Session saved. Retry Telegram bot / FB link.")


if __name__ == "__main__":
    asyncio.run(main())
