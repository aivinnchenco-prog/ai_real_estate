"""Playwright browser session for Facebook Marketplace checks."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from playwright.sync_api import BrowserContext, Page, sync_playwright

from ..app.config import AvailabilityConfig


FB_PARSER_ROOT = Path(__file__).resolve().parents[2] / "agent_1_parser/fb_parser"


def resolve_facebook_profile(config: AvailabilityConfig) -> Path:
    raw = (
        os.environ.get("FB_BROWSER_PROFILE", "").strip()
        or str(config.facebook_browser_profile)
    )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        # Match agent1b.fb_session: relative profiles live under fb_parser/.
        path = (FB_PARSER_ROOT / path).resolve()
    else:
        path = path.resolve()
    return path


def resolve_facebook_storage_state(config: AvailabilityConfig) -> Path | None:
    raw = os.environ.get("AVAILABILITY_FACEBOOK_STORAGE_STATE", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.exists() else None
    default = Path(__file__).resolve().parents[2] / "agent_1_parser/fb_parser/fb_storage_state.json"
    return default if default.exists() else None


@contextmanager
def facebook_browser_page(
    config: AvailabilityConfig,
    *,
    headless: bool = True,
) -> Generator[Page, None, None]:
    """One Facebook browser page. Uses persistent profile or storage_state fallback."""
    profile = resolve_facebook_profile(config)
    storage = resolve_facebook_storage_state(config)
    with sync_playwright() as p:
        args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        if profile.exists() and any(profile.iterdir()):
            ctx = p.chromium.launch_persistent_context(
                str(profile),
                headless=headless,
                args=args,
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                yield page
            finally:
                ctx.close()
        elif storage:
            browser = p.chromium.launch(headless=headless, args=args)
            ctx = browser.new_context(
                storage_state=str(storage),
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            try:
                yield page
            finally:
                ctx.close()
                browser.close()
        else:
            raise RuntimeError(
                "Facebook browser session missing: set FB_BROWSER_PROFILE or fb_storage_state.json"
            )


def fetch_marketplace_page(
    url: str,
    config: AvailabilityConfig,
    *,
    timeout_ms: int = 90000,
    settle_s: float = 6.0,
) -> tuple[str, str, str]:
    """Return (final_url, html, body_text)."""
    with facebook_browser_page(config) as page:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        time.sleep(settle_s)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        return page.url, page.content(), page.inner_text("body")
