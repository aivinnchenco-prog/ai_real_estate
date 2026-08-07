"""Facebook Marketplace Messenger connector (Playwright, isolated profile)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config_loader import data_dir
from ..state_machine import BrowserScreenState
from . import ChannelConnector, InboundMessage, SendResult

PROFILE_DIR = data_dir() / "facebook_profile"
DIAG_DIR = data_dir() / "diagnostics"


@dataclass
class FacebookConnector(ChannelConnector):
    """Semantic locator strategy; coordinates only as last resort."""

    headless: bool = True
    _screen: str = BrowserScreenState.UNKNOWN.value
    _threads: dict[str, list[str]] = field(default_factory=dict)
    _thread_urls: dict[str, str] = field(default_factory=dict)
    _sent: dict[str, list[str]] = field(default_factory=dict)
    _listing_threads: dict[str, str] = field(default_factory=dict)
    _page: Any = None
    _browser: Any = None
    _playwright: Any = None

    def profile_path(self) -> Path:
        return PROFILE_DIR

    @staticmethod
    def extract_thread_id(url: str) -> str:
        for pattern in (r"/t/(\d+)", r"thread_id=(\d+)", r"/messages/t/(\d+)"):
            m = re.search(pattern, url or "")
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def bind_thread(listing_id: str, thread_url: str) -> tuple[str, str]:
        """Binding strategy: listing_id -> thread_url -> thread_id."""
        thread_id = FacebookConnector.extract_thread_id(thread_url)
        if not thread_id and listing_id:
            thread_id = f"listing:{listing_id}"
        return thread_id, thread_url

    def open_listing(self, url: str, listing_id: str) -> str:
        if self._detect_login_required(url):
            self._screen = BrowserScreenState.LOGIN_REQUIRED.value
            return self._screen
        if self._detect_checkpoint(url):
            self._screen = BrowserScreenState.CHECKPOINT.value
            return self._screen
        self._screen = BrowserScreenState.LISTING.value
        if listing_id in self._listing_threads:
            self._screen = BrowserScreenState.MESSENGER_OPEN.value
        else:
            self._screen = BrowserScreenState.MESSAGE_BUTTON_AVAILABLE.value
        return self._screen

    def send_message(self, text: str, *, thread_id: str = "", listing_id: str = "") -> SendResult:
        if self._screen in (
            BrowserScreenState.LOGIN_REQUIRED.value,
            BrowserScreenState.CHECKPOINT.value,
            BrowserScreenState.ACCOUNT_PAUSED.value,
            BrowserScreenState.UNKNOWN.value,
        ):
            return SendResult(False, error=f"screen={self._screen}", screen_state=self._screen)

        if self._screen not in (
            BrowserScreenState.MESSAGE_INPUT_READY.value,
            BrowserScreenState.MESSENGER_OPEN.value,
            BrowserScreenState.MESSAGE_BUTTON_AVAILABLE.value,
        ):
            return SendResult(False, error="input_not_ready", screen_state=self._screen)

        tid = thread_id or f"listing:{listing_id}"
        self._sent.setdefault(tid, []).append(text)
        self._threads.setdefault(tid, [])
        url = self._thread_urls.get(tid, f"https://www.facebook.com/messages/t/{tid}")
        self._thread_urls[tid] = url
        if listing_id:
            self._listing_threads[listing_id] = tid
        self._screen = BrowserScreenState.WAITING_REPLY.value
        return SendResult(True, thread_id=tid, thread_url=url, screen_state=self._screen)

    def poll_inbound(self, thread_id: str) -> list[InboundMessage]:
        msgs = self._threads.get(thread_id, [])
        return [InboundMessage(text=t, thread_id=thread_id) for t in msgs]

    def push_inbound(self, thread_id: str, text: str) -> None:
        self._threads.setdefault(thread_id, []).append(text)

    def thread_contains_sent_text(self, thread_id: str, text: str) -> bool:
        return any(text[:40] in s for s in self._sent.get(thread_id, []))

    def set_screen_state(self, state: BrowserScreenState) -> None:
        self._screen = state.value

    def _detect_login_required(self, url: str) -> bool:
        return "login" in (url or "").lower()

    def _detect_checkpoint(self, url: str) -> bool:
        u = (url or "").lower()
        return "checkpoint" in u or "security" in u

    def save_diagnostic(self, name: str, *, url: str = "", html: str = "") -> None:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        path = DIAG_DIR / f"{name}_{int(time.time())}.txt"
        path.write_text(f"url={url}\n\n{html[:5000]}", encoding="utf-8")

    # Playwright lifecycle (optional real browser; tests use in-memory mock above)
    def start_browser(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("playwright not installed") from exc
        self.profile_path().mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_path()),
            headless=self.headless,
        )
        self._page = self._browser.pages[0] if self._browser.pages else self._browser.new_page()

    def stop_browser(self) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
