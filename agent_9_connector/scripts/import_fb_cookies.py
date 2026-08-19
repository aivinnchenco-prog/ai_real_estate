#!/usr/bin/env python3
"""Import Facebook cookies/localStorage into Agent 9 persistent profile."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for env_path in (ROOT / ".env", Path("/opt/openhome/.env"), Path("/opt/openhome/app/.env")):
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from agent9_connector.config_loader import facebook_profile_dir, legacy_facebook_profile_dir
from agent9_connector.connectors import facebook_playwright as pw
from agent9_connector.connectors.facebook import FacebookConnector


def default_source() -> Path:
    env_src = os.environ.get("AGENT9_FB_STORAGE_STATE", "").strip()
    candidates = []
    if env_src:
        candidates.append(Path(env_src))
    candidates.extend([
        Path("/opt/openhome/app/agent_1_parser/fb_parser/fb_storage_state.json"),
        ROOT.parent / "agent_1_parser" / "fb_parser" / "fb_storage_state.json",
    ])
    for path in candidates:
        if path.exists():
            return path
    raise SystemExit("No fb_storage_state.json found — set AGENT9_FB_STORAGE_STATE")


def main() -> int:
    src = default_source()
    state = json.loads(src.read_text(encoding="utf-8"))
    cookies = state.get("cookies") or []
    if not cookies:
        raise SystemExit(f"No cookies in {src}")
    if not any(c.get("name") == "c_user" for c in cookies):
        raise SystemExit("Storage state missing c_user cookie")

    profile = facebook_profile_dir()
    print(f"Importing {len(cookies)} cookies from {src} -> {profile}")
    print(f"Legacy backup path (unchanged): {legacy_facebook_profile_dir()}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ctx.add_cookies(cookies)
        for origin in state.get("origins") or []:
            origin_url = origin.get("origin")
            items = origin.get("localStorage") or []
            if not origin_url or not items:
                continue
            page.goto(origin_url, wait_until="domcontentloaded", timeout=90000)
            for item in items:
                name = item.get("name")
                if not name:
                    continue
                page.evaluate(
                    "(pair) => localStorage.setItem(pair.name, pair.value)",
                    {"name": name, "value": item.get("value") or ""},
                )

        listing = os.environ.get(
            "AGENT9_FB_VERIFY_URL",
            "https://www.facebook.com/marketplace/item/1515735863510647",
        )
        page.goto(listing, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)
        security = pw.detect_security_state(page.url, page.inner_text("body")[:8000])
        names = {c["name"] for c in ctx.cookies()}
        ctx.close()

    if security:
        raise SystemExit(f"Import verify failed: {security}")
    if "c_user" not in names:
        raise SystemExit("Import failed: c_user not persisted")
    print("OK: Agent 9 Facebook session imported and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
