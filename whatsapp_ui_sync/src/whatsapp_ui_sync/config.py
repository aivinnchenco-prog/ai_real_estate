"""Environment / config for optional WhatsApp UI list sync."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def default_profile_dir() -> Path:
    override = (os.getenv("WHATSAPP_UI_PROFILE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    # Native Chrome profile (CDP). Do not reuse Playwright-launched profile.
    return Path.home() / ".openhome" / "whatsapp_ui_chrome_native"


def default_queue_dir() -> Path:
    override = (os.getenv("WHATSAPP_UI_QUEUE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openhome" / "whatsapp_ui_sync"


@dataclass(frozen=True)
class WhatsAppUiSyncConfig:
    enabled: bool = False
    dry_run: bool = True
    headless: bool = False
    profile_dir: Path = Path.home() / ".openhome" / "whatsapp_ui_chrome_native"
    queue_dir: Path = Path.home() / ".openhome" / "whatsapp_ui_sync"
    client_list_name: str = "Client"
    owner_list_name: str = "Owner"
    agent_list_name: str = "Owner"
    action_delay_ms: int = 2000
    max_attempts: int = 3
    page_load_timeout_ms: int = 90_000
    ready_extra_wait_ms: int = 5000
    # "chrome" = installed Google Chrome (preferred; same WA UI as manual).
    # "chromium" = Playwright bundled browser.
    browser_channel: str = "chrome"
    locale: str = "ru-RU"
    # If set (e.g. http://127.0.0.1:9222), attach to an already-running
    # normal Chrome via CDP instead of Playwright-launching a browser.
    # This preserves the real Chrome UI (Lists) without automation flags.
    cdp_url: str = ""
    whatsapp_url: str = "https://web.whatsapp.com/"

    @classmethod
    def from_env(cls) -> WhatsAppUiSyncConfig:
        channel = (os.getenv("WHATSAPP_UI_BROWSER_CHANNEL") or "chrome").strip().lower()
        if channel in {"", "auto"}:
            channel = "chrome"
        if channel not in {"chrome", "chromium", "chrome-beta", "msedge"}:
            channel = "chrome"
        return cls(
            enabled=_env_bool("WHATSAPP_UI_SYNC_ENABLED", False),
            dry_run=_env_bool("WHATSAPP_UI_DRY_RUN", True),
            headless=_env_bool("WHATSAPP_UI_HEADLESS", False),
            profile_dir=default_profile_dir(),
            queue_dir=default_queue_dir(),
            client_list_name=(os.getenv("WHATSAPP_UI_CLIENT_LIST_NAME") or "Client").strip()
            or "Client",
            owner_list_name=(os.getenv("WHATSAPP_UI_OWNER_LIST_NAME") or "Owner").strip()
            or "Owner",
            agent_list_name=(os.getenv("WHATSAPP_UI_AGENT_LIST_NAME") or "Owner").strip()
            or "Owner",
            action_delay_ms=max(0, _env_int("WHATSAPP_UI_ACTION_DELAY_MS", 2000)),
            max_attempts=max(1, min(5, _env_int("WHATSAPP_UI_MAX_ATTEMPTS", 3))),
            page_load_timeout_ms=max(
                5_000, _env_int("WHATSAPP_UI_PAGE_LOAD_TIMEOUT_MS", 90_000)
            ),
            ready_extra_wait_ms=max(
                0, _env_int("WHATSAPP_UI_READY_EXTRA_WAIT_MS", 5000)
            ),
            browser_channel=channel,
            locale=(os.getenv("WHATSAPP_UI_LOCALE") or "ru-RU").strip() or "ru-RU",
            cdp_url=(os.getenv("WHATSAPP_UI_CDP_URL") or "").strip(),
        )

    def writes_allowed(self, *, confirm_write: bool = False) -> bool:
        """Real membership changes require enabled + dry_run=false + confirm."""
        return bool(self.enabled and not self.dry_run and confirm_write)
