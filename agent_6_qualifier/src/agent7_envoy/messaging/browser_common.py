"""Shared Playwright helpers for Agent7 source-native owner transports."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from agent7_envoy.messaging.base import AuthStatus, browser_failures_root


def message_fingerprint(text: str, *, ts: str = "") -> str:
    raw = f"{ts}|{(text or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def save_failure_screenshot(page: Any, prefix: str) -> str:
    try:
        root = browser_failures_root()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = root / f"{prefix}_{stamp}.png"
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return ""


def launch_persistent_context(
    *,
    profile_dir: Path,
    headless: bool = False,
    locale: str = "en-US",
):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright not installed; pip install playwright && playwright install chromium"
        ) from exc

    profile_dir.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "viewport": {"width": 1400, "height": 900},
        "locale": locale,
        "args": ["--disable-dev-shm-usage"],
        "ignore_default_args": ["--enable-automation"],
    }
    try:
        context = pw.chromium.launch_persistent_context(**launch_kwargs)
    except Exception:
        launch_kwargs.pop("channel", None)
        context = pw.chromium.launch_persistent_context(**launch_kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    return pw, context, page


def click_first_matching(
    page: Any,
    *,
    roles: Sequence[tuple[str, str]] = (),
    texts: Sequence[str] = (),
    selectors: Sequence[str] = (),
    timeout_ms: int = 2500,
) -> bool:
    """Try role/aria → visible text → CSS selectors. Returns True if clicked."""
    for role, name in roles:
        try:
            loc = page.get_by_role(role, name=re.compile(name, re.I))
            if loc.count() > 0:
                loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            pass
    for text in texts:
        try:
            loc = page.get_by_text(re.compile(text, re.I))
            if loc.count() > 0:
                loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            pass
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            pass
    return False


def find_composer(
    page: Any,
    *,
    placeholders: Sequence[str] = (),
    selectors: Sequence[str] = (),
) -> Any | None:
    for ph in placeholders:
        try:
            loc = page.get_by_placeholder(re.compile(ph, re.I))
            if loc.count() > 0:
                return loc.first
        except Exception:
            pass
    for role_name in ("textbox", "searchbox"):
        try:
            loc = page.get_by_role(role_name)
            if loc.count() > 0:
                # Prefer contenteditable / message boxes near bottom
                return loc.last
        except Exception:
            pass
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                return loc.first
        except Exception:
            pass
    # contenteditable fallback
    try:
        loc = page.locator('[contenteditable="true"]')
        if loc.count() > 0:
            return loc.last
    except Exception:
        pass
    return None


def classify_auth_from_url(url: str, *, login_hints: Sequence[str]) -> AuthStatus:
    low = (url or "").lower()
    for hint in login_hints:
        if hint.lower() in low:
            return AuthStatus.AUTH_REQUIRED
    return AuthStatus.READY
