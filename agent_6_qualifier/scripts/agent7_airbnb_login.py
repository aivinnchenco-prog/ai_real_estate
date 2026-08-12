#!/usr/bin/env python3
"""Open Airbnb browser profile for manual Agent7 owner-outreach login.

No password prompts. User signs in manually; authenticated profile is persisted.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent7_envoy.messaging.airbnb_messages import (  # noqa: E402
    LOGIN_URL_HINTS,
    AirbnbMessagesOwnerTransport,
)
from agent7_envoy.messaging.browser_common import (  # noqa: E402
    classify_auth_from_url,
    launch_persistent_context,
)


def main() -> int:
    transport = AirbnbMessagesOwnerTransport(headless=False)
    profile = transport.profile_dir
    assert profile is not None
    print(f"Airbnb profile: {profile}")
    print("Opening browser — log in manually. Do not enter passwords in this terminal.")
    pw, context, page = launch_persistent_context(
        profile_dir=profile, headless=False
    )
    try:
        page.goto("https://www.airbnb.com/login", wait_until="domcontentloaded")
        print("Waiting for authenticated Airbnb session…")
        deadline = time.time() + 600
        while time.time() < deadline:
            # After login Airbnb usually lands on homepage / trips / wishlist
            url = (page.url or "").lower()
            auth = classify_auth_from_url(url, login_hints=LOGIN_URL_HINTS)
            if "login" not in url and auth.value != "AUTH_REQUIRED":
                try:
                    page.goto(
                        "https://www.airbnb.com/guest/inbox",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    url2 = (page.url or "").lower()
                    if "login" not in url2:
                        transport.mark_auth_ok()
                        print("AUTH READY — profile saved.")
                        print("AIRBNB_OWNER_MESSAGING: READY")
                        return 0
                except Exception:
                    pass
            time.sleep(3)
        print("AUTH_REQUIRED — timed out waiting for login.")
        print("AIRBNB_OWNER_MESSAGING: AUTH_REQUIRED")
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
