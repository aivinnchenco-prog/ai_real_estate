#!/usr/bin/env python3
"""Manual Facebook login for Agent 9 persistent profile."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent9_connector.connectors.facebook import FacebookConnector


def main() -> int:
    fb = FacebookConnector(headless=False)
    print(f"Opening browser with profile: {fb.profile_path()}")
    print("Log in to Facebook manually, then close the browser window.")
    fb.start_browser()
    if fb._page:
        fb._page.goto("https://www.facebook.com/")
        input("Press Enter after login is complete...")
    fb.stop_browser()
    print("Profile saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
