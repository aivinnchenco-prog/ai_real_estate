#!/usr/bin/env python3
"""Validate / refresh Facebook session for Agent7 owner outreach.

Reuses the same Playwright profile as Agent1 FB Marketplace parser
(agent_1_parser/fb_parser/.fb_profile) — not a separate outreach profile.

No password prompts. If session already has c_user cookie → READY.
Otherwise opens the shared profile for manual login.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent7_envoy.messaging.browser_common import (  # noqa: E402
    classify_auth_from_url,
    launch_persistent_context,
)
from agent7_envoy.messaging.facebook_messenger import (  # noqa: E402
    LOGIN_URL_HINTS,
    FacebookMessengerOwnerTransport,
)
from agent7_envoy.messaging.fb_profile import (  # noqa: E402
    facebook_profile_has_session,
    resolve_agent7_facebook_profile_dir,
)


def main() -> int:
    profile = resolve_agent7_facebook_profile_dir()
    transport = FacebookMessengerOwnerTransport(profile_dir=profile, headless=False)
    print(f"Facebook profile (shared with FB Marketplace parser): {profile}")

    if facebook_profile_has_session(profile):
        transport.mark_auth_ok()
        print("Existing parser session detected (c_user cookie).")
        print("AUTH READY — reusing Agent1 FB Marketplace profile.")
        print("FACEBOOK_OWNER_MESSAGING: READY")
        return 0

    print("No saved Facebook session in parser profile.")
    print("Opening browser — log in manually. Do not enter passwords in this terminal.")
    print("This writes into the SAME profile the Marketplace parser uses.")
    pw, context, page = launch_persistent_context(
        profile_dir=profile, headless=False
    )
    try:
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        print("Waiting for authenticated Facebook session…")
        deadline = time.time() + 600
        while time.time() < deadline:
            auth = classify_auth_from_url(page.url, login_hints=LOGIN_URL_HINTS)
            if auth.value != "AUTH_REQUIRED" and "login" not in (page.url or "").lower():
                try:
                    page.goto(
                        "https://www.facebook.com/marketplace/",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    auth2 = classify_auth_from_url(page.url, login_hints=LOGIN_URL_HINTS)
                    if auth2.value != "AUTH_REQUIRED":
                        transport.mark_auth_ok()
                        print("AUTH READY — profile saved into parser .fb_profile.")
                        print("FACEBOOK_OWNER_MESSAGING: READY")
                        return 0
                except Exception:
                    pass
            time.sleep(3)
        print("AUTH_REQUIRED — timed out waiting for login.")
        print("FACEBOOK_OWNER_MESSAGING: AUTH_REQUIRED")
        return 2
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
