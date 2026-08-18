from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..dotenv_util import package_root
from .backend import load_fb_browser_config
from .profile import facebook_profile_dir, facebook_profile_lock, resolve_headless


def _browser_settings() -> dict[str, Any]:
    return load_fb_browser_config().get("browser") or {}


@contextlib.contextmanager
def browser_context() -> Iterator[Any]:
    """Persistent Chromium context on Agent 7 Facebook profile."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright не установлен. pip install playwright && playwright install chromium"
        ) from exc

    profile = facebook_profile_dir()
    if not profile.exists():
        raise RuntimeError(
            f"Профиль Agent 7 не найден: {profile}. "
            "Авторизуйте аккаунт Agent 6/7: "
            "agent_6_qualifier/scripts/facebook_agent7_login.py "
            "(не FB_EMAIL парсера Agent 1)."
        )

    browser_cfg = _browser_settings()
    viewport = browser_cfg.get("viewport") or {"width": 1280, "height": 900}
    launch_kwargs: dict[str, Any] = {
        "headless": resolve_headless(),
        "viewport": {
            "width": int(viewport.get("width", 1280)),
            "height": int(viewport.get("height", 900)),
        },
        "locale": browser_cfg.get("locale") or "ru-RU",
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
        ],
    }

    debug_dir = package_root() / "data" / "fb_browser" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    with facebook_profile_lock(profile):
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **launch_kwargs,
            )
            timeout = int(browser_cfg.get("action_timeout_ms", 30000))
            context.set_default_timeout(timeout)
            try:
                yield context
            finally:
                context.close()


def is_logged_in(context: Any) -> bool:
    return any(
        c.get("name") == "c_user" and "facebook.com" in c.get("domain", "")
        for c in context.cookies()
    )


def ensure_logged_in(page: Any, context: Any) -> None:
    page.goto(
        "https://www.facebook.com/",
        wait_until="domcontentloaded",
        timeout=int(_browser_settings().get("nav_timeout_ms", 90000)),
    )
    page.wait_for_timeout(1500)
    if is_logged_in(context):
        return
    url = (page.url or "").lower()
    body = ""
    try:
        body = (page.inner_text("body")[:8000] or "").lower()
    except Exception:
        pass
    if "login" in url and "facebook.com" in url:
        raise RuntimeError("AUTH_REQUIRED: LOGIN_REQUIRED")
    if "checkpoint" in url or "security" in url or "captcha" in body:
        raise RuntimeError("AUTH_REQUIRED: CHECKPOINT")
    if "temporarily blocked" in body or "ограничен" in body:
        raise RuntimeError("AUTH_REQUIRED: ACCOUNT_PAUSED")
    raise RuntimeError(
        "AUTH_REQUIRED: нет c_user в профиле Agent 7. "
        "Войдите через agent_6_qualifier/scripts/facebook_agent7_login.py "
        "(аккаунт Agent 6/7, не FB_EMAIL парсера)."
    )


def agent4_groups_cfg(publisher_cfg: dict[str, Any]) -> dict[str, Any]:
    """Cfg dict compatible with agent_4_publisher fb_groups_pipeline helpers."""
    fb = load_fb_browser_config()
    browser = dict(fb.get("browser") or {})
    limits = dict(fb.get("limits") or {})
    media = dict((publisher_cfg.get("media") or {}))
    groups = dict(publisher_cfg.get("fb_groups") or {})
    return {
        "browser": browser,
        "limits": limits,
        "media": {
            "max_images": groups.get("max_images")
            or media.get("max_groups_images")
            or 10,
            "hook_cover_first": media.get("hook_cover_first", True),
        },
    }


def agent4_marketplace_cfg(publisher_cfg: dict[str, Any]) -> dict[str, Any]:
    fb = load_fb_browser_config()
    mp_pub = dict(publisher_cfg.get("fb_marketplace") or {})
    mp = dict(fb.get("marketplace") or {})
    browser = dict(fb.get("browser") or {})
    return {
        "browser": browser,
        "create_url": mp.get("create_url"),
        "selling_url": mp.get("selling_url"),
        "listing": {
            "location_suffix": mp_pub.get("location_suffix") or "Phuket",
            "property_type_by_housing": {
                "Вилла": "house",
                "Дом": "house",
                "Таунхаус": "townhouse",
                "Кондо": "apartment",
                "Квартира": "apartment",
                "Апартаменты": "apartment",
                "Комната": "room",
                "default": "house",
            },
        },
        "media": {
            "max_images": mp_pub.get("max_images")
            or (publisher_cfg.get("media") or {}).get("max_marketplace_images")
            or 8,
        },
    }


def _agent4_scripts() -> Path:
    scripts = package_root().parent / "agent_4_publisher" / "scripts"
    if not scripts.is_dir():
        raise RuntimeError(f"agent_4_publisher/scripts не найден: {scripts}")
    return scripts


def import_agent4_groups():
    import sys

    path = str(_agent4_scripts())
    if path not in sys.path:
        sys.path.insert(0, path)
    import fb_groups_pipeline as mod

    return mod


def import_agent4_marketplace():
    import sys

    path = str(_agent4_scripts())
    if path not in sys.path:
        sys.path.insert(0, path)
    import fb_marketplace_pipeline as mod

    return mod
