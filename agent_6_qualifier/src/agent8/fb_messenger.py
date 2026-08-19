from __future__ import annotations

import os
import re

from agent7.profile_lock import facebook_profile_dir, facebook_profile_lock

_LISTING_RE = re.compile(r"/marketplace/item/(\d+)")


def extract_listing_id(url: str) -> str:
    m = _LISTING_RE.search(url or "")
    return m.group(1) if m else ""


def send_fb_marketplace_message(facebook_url: str, text: str) -> tuple[bool, str]:
    """Open listing and send first message to owner via Messenger."""
    listing_id = extract_listing_id(facebook_url)
    if not listing_id:
        return False, "missing_listing_id"
    if not text.strip():
        return False, "empty_message"

    enabled = os.getenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", "false").strip().lower()
    if enabled not in ("1", "true", "yes"):
        return False, "messenger_disabled"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright_not_installed"

    profile = facebook_profile_dir()
    headless = __import__("os").getenv("AGENT7_FB_HEADLESS", "true").lower() in ("1", "true", "yes")

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
            try:
                page.goto(facebook_url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(2000)
                from . import fb_playwright as pw

                security = pw.detect_security_state(page.url, page.inner_text("body")[:8000])
                if security:
                    return False, security

                if not pw.click_message_button(page):
                    return False, "message_button_not_found"
                page.wait_for_timeout(1000)
                if not pw.type_and_send(page, text):
                    return False, "send_failed"
                return True, ""
            finally:
                ctx.close()
