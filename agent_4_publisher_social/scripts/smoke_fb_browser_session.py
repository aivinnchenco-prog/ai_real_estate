#!/usr/bin/env python3
"""Smoke-check browser FB backend (Agent 7 profile only)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent))

from publisher_social.dotenv_util import load_dotenv

load_dotenv()
os.environ.setdefault("PUBLISHER_FB_BACKEND", "browser")

from publisher_social.browser.backend import channel_uses_browser, fb_backend
from publisher_social.browser.profile import (
    DEFAULT_AGENT7_PROFILE_NAME,
    facebook_profile_dir,
    resolve_headless,
)
from publisher_social.config import load_publisher_config

_ACCOUNT_HINT_RE = re.compile(r"TripHome|Karen Rai|agent.?1|parser", re.I)


def _account_hint(page_text: str) -> str | None:
    for line in page_text.splitlines():
        line = line.strip()
        if line and _ACCOUNT_HINT_RE.search(line):
            return line[:80]
    return None


def main() -> int:
    cfg = load_publisher_config()
    backend = fb_backend(cfg)
    try:
        profile = facebook_profile_dir()
    except RuntimeError as exc:
        print(f"STATUS: PROFILE_INVALID ({exc})")
        return 2
    print(f"backend: {backend}")
    print(f"profile_name: {profile.name} (required: {DEFAULT_AGENT7_PROFILE_NAME})")
    print(f"profile: {profile}")
    print(f"profile_exists: {profile.exists()}")
    print(f"headless: {resolve_headless()}")

    if backend != "browser":
        print("STATUS: BACKEND_NOT_BROWSER (set PUBLISHER_FB_BACKEND=browser)")
        return 2
    if not profile.exists():
        print("STATUS: PROFILE_MISSING")
        print("Login: agent_6_qualifier/scripts/facebook_agent7_login.py")
        return 2

    from publisher_social.browser.session import browser_context, ensure_logged_in, is_logged_in

    try:
        with browser_context() as context:
            page = context.pages[0] if context.pages else context.new_page()
            ensure_logged_in(page, context)
            logged = is_logged_in(context)
            body = ""
            try:
                body = page.inner_text("body")[:4000]
            except Exception:
                pass
            hint = _account_hint(body)
            print(f"url: {page.url[:120]}")
            print(f"logged_in: {logged}")
            if hint:
                print(f"account_hint: {hint}")
                print("WARN: похоже на чужой аккаунт — проверьте, что это Agent 6/7")
    except Exception as exc:
        print(f"STATUS: ERROR ({exc})")
        return 1

    print("STATUS: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
