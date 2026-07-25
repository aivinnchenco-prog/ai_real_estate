#!/usr/bin/env python3
"""Facebook session helpers: login, proxy, human-like delays, profile lock."""

from __future__ import annotations

import asyncio
import os
import random
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


_PROFILE_LOCK = asyncio.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ProxySettings:
    server: str
    username: Optional[str] = None
    password: Optional[str] = None

    def to_playwright(self) -> dict[str, str]:
        out: dict[str, str] = {"server": self.server}
        if self.username:
            out["username"] = self.username
        if self.password:
            out["password"] = self.password
        return out

    def to_crawl4ai(self):
        from crawl4ai.async_configs import ProxyConfig

        return ProxyConfig(server=self.server, username=self.username, password=self.password)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


async def human_delay(label: str = "") -> None:
    lo = env_int("FB_DELAY_MIN_MS", 800) / 1000.0
    hi = env_int("FB_DELAY_MAX_MS", 2200) / 1000.0
    if hi < lo:
        hi = lo
    await asyncio.sleep(random.uniform(lo, hi))


def parse_proxy_from_env() -> Optional[ProxySettings]:
    raw = os.getenv("FB_PROXY", "").strip()
    if not raw:
        host = os.getenv("FB_PROXY_HOST", "").strip()
        port = os.getenv("FB_PROXY_PORT", "").strip()
        if host and port:
            scheme = os.getenv("FB_PROXY_SCHEME", "http").strip() or "http"
            user = os.getenv("FB_PROXY_USER", "").strip()
            pwd = os.getenv("FB_PROXY_PASS", "").strip()
            if user:
                raw = f"{scheme}://{user}:{pwd}@{host}:{port}"
            else:
                raw = f"{scheme}://{host}:{port}"

    if not raw:
        return None

    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"Invalid FB_PROXY value: {raw}")

    port = f":{parsed.port}" if parsed.port else ""
    server = f"{parsed.scheme}://{parsed.hostname}{port}"
    return ProxySettings(
        server=server,
        username=parsed.username or None,
        password=parsed.password or None,
    )


def get_profile_path() -> Path:
    raw = os.getenv("FB_BROWSER_PROFILE", ".fb_profile").strip() or ".fb_profile"
    if "@" in raw or raw.count("/") > 2:
        raise ValueError(
            "FB_BROWSER_PROFILE must be a folder path (e.g. .fb_profile), "
            "not email/password. Put credentials into FB_EMAIL and FB_PASSWORD."
        )
    path = Path(raw)
    if not path.is_absolute():
        # Anchor to project root so the profile is found regardless of cwd
        # (systemd, cron, launching from ~, etc.).
        path = _PROJECT_ROOT / path
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_headless(for_login: bool = False) -> bool:
    if for_login:
        env = os.getenv("FB_HEADLESS", "").strip().lower()
        if env == "true":
            return True
        if env == "false":
            return False
        return False
    # Parser/TG bot: headless is safer (no GUI from background process).
    parser_env = os.getenv("FB_HEADLESS_PARSER", "true").strip().lower()
    return parser_env != "false"


def has_saved_session(profile_path: Path) -> bool:
    cookies_db = profile_path / "Default" / "Cookies"
    if not cookies_db.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM cookies WHERE host_key LIKE '%facebook.com%' AND name='c_user' LIMIT 1"
        )
        ok = cur.fetchone() is not None
        conn.close()
        return ok
    except Exception:
        return cookies_db.stat().st_size > 10_000


def build_browser_kwargs(for_login: bool = False) -> dict[str, Any]:
    profile_path = str(get_profile_path())
    kwargs: dict[str, Any] = {
        "headless": resolve_headless(for_login=for_login),
        "verbose": False,
        "enable_stealth": True,
        "viewport_width": 1440,
        "viewport_height": 900,
        "user_data_dir": profile_path,
        "use_persistent_context": True,
    }
    proxy = parse_proxy_from_env()
    if proxy:
        kwargs["proxy_config"] = proxy.to_crawl4ai()
    return kwargs


@asynccontextmanager
async def profile_lock():
    await _PROFILE_LOCK.acquire()
    lock_file = get_profile_path() / ".profile.lock"
    for _ in range(30):
        try:
            if not lock_file.exists():
                lock_file.write_text(str(os.getpid()), encoding="utf-8")
                break
            await asyncio.sleep(0.5)
        except OSError:
            await asyncio.sleep(0.5)
    try:
        yield
    finally:
        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass
        _PROFILE_LOCK.release()


async def is_logged_in(page) -> bool:
    url = page.url.lower()
    if "login" in url or "checkpoint" in url:
        return False
    body = (await page.content()).lower()
    login_markers = [
        "log into facebook",
        "email or mobile number",
        "email or phone",
        "เข้าสู่ระบบ",
        "увійти",
        "войти",
    ]
    if any(m in body for m in login_markers):
        return False
    cookies = await page.context.cookies()
    if any(c.get("name") == "c_user" and "facebook.com" in c.get("domain", "") for c in cookies):
        return True
    return False


async def perform_login(page, email: str, password: str) -> None:
    await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=90000)
    await human_delay()

    email_sel = 'input[name="email"], input#email, input[type="email"]'
    pass_sel = 'input[name="pass"], input#pass, input[type="password"]'

    await page.wait_for_selector(email_sel, timeout=30000)
    await human_delay()
    await page.fill(email_sel, email)
    await human_delay()
    await page.fill(pass_sel, password)
    await human_delay()

    login_btn = page.locator(
        'button[name="login"], button[type="submit"], input[type="submit"]'
    )
    if await login_btn.count():
        try:
            await login_btn.first.click(timeout=5000)
        except Exception:
            await page.locator(pass_sel).press("Enter")
    else:
        await page.locator(pass_sel).press("Enter")

    await page.wait_for_load_state("domcontentloaded", timeout=90000)
    await human_delay("post-login")

    if "checkpoint" in page.url.lower():
        raise RuntimeError(
            "AUTH_REQUIRED: Facebook checkpoint/2FA. Run login_fb.py with FB_HEADLESS=false and finish manually."
        )


async def ensure_facebook_session(for_login: bool = False) -> None:
    """Login once and persist cookies in profile. Use for login_fb.py or when session missing."""
    email = os.getenv("FB_EMAIL", "").strip()
    password = os.getenv("FB_PASSWORD", "").strip()
    profile_path = get_profile_path()

    if has_saved_session(profile_path) and not for_login:
        return

    if not email or not password:
        raise RuntimeError(
            "AUTH_REQUIRED: Set FB_EMAIL and FB_PASSWORD in .env, then run: python agent1b/login_fb.py"
        )

    from playwright.async_api import async_playwright

    proxy = parse_proxy_from_env()

    async with profile_lock():
        async with async_playwright() as p:
            launch_args: dict[str, Any] = {
                "headless": resolve_headless(for_login=True),
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            }
            if proxy:
                launch_args["proxy"] = proxy.to_playwright()

            context = None
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    context = await p.chromium.launch_persistent_context(
                        str(profile_path),
                        **launch_args,
                    )
                    break
                except Exception as e:
                    last_err = e
                    await asyncio.sleep(1.5 * (attempt + 1))
            if context is None:
                raise RuntimeError(f"AUTH_REQUIRED: Cannot open browser profile: {last_err}")

            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    "https://www.facebook.com/marketplace/",
                    wait_until="domcontentloaded",
                    timeout=90000,
                )
                await human_delay()

                if not await is_logged_in(page):
                    await perform_login(page, email, password)
                    if not await is_logged_in(page):
                        raise RuntimeError(
                            "AUTH_REQUIRED: Login failed. Check FB_EMAIL/FB_PASSWORD or complete 2FA manually."
                        )
            finally:
                await context.close()
                await asyncio.sleep(1.0)
