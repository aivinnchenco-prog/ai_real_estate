"""Facebook Marketplace Messenger connector (Playwright, isolated profile)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config_loader import data_dir, load_connector_config
from ..state_machine import BrowserScreenState
from . import ChannelConnector, InboundMessage, SendResult
from . import facebook_playwright as pw

PROFILE_DIR = data_dir() / "facebook_profile"
DIAG_DIR = data_dir() / "diagnostics"


@dataclass
class FacebookConnector(ChannelConnector):
    """Semantic locator strategy; mock_mode for offline tests."""

    headless: bool = True
    mock_mode: bool = True
    _screen: str = BrowserScreenState.UNKNOWN.value
    _threads: dict[str, list[str]] = field(default_factory=dict)
    _thread_urls: dict[str, str] = field(default_factory=dict)
    _sent: dict[str, list[str]] = field(default_factory=dict)
    _listing_threads: dict[str, str] = field(default_factory=dict)
    _last_seen: dict[str, set[str]] = field(default_factory=dict)
    _current_listing_url: str = ""
    _page: Any = None
    _browser: Any = None
    _playwright: Any = None
    _browser_started: bool = False

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
        thread_id = FacebookConnector.extract_thread_id(thread_url)
        if not thread_id and listing_id:
            thread_id = f"listing:{listing_id}"
        return thread_id, thread_url

    def ensure_browser(self) -> None:
        if self.mock_mode or self._browser_started:
            return
        self.start_browser()
        self._browser_started = True

    def open_listing(self, url: str, listing_id: str) -> str:
        if self.mock_mode:
            return self._open_listing_mock(url, listing_id)
        return self._open_listing_live(url, listing_id)

    def send_message(self, text: str, *, thread_id: str = "", listing_id: str = "") -> SendResult:
        if self.mock_mode:
            return self._send_message_mock(text, thread_id=thread_id, listing_id=listing_id)
        return self._send_message_live(text, thread_id=thread_id, listing_id=listing_id)

    def poll_inbound(self, thread_id: str) -> list[InboundMessage]:
        if self.mock_mode:
            msgs = self._threads.get(thread_id, [])
            return [InboundMessage(text=t, thread_id=thread_id) for t in msgs]
        return self._poll_inbound_live(thread_id)

    def push_inbound(self, thread_id: str, text: str) -> None:
        self._threads.setdefault(thread_id, []).append(text)

    def thread_contains_sent_text(self, thread_id: str, text: str) -> bool:
        if any(text[:40] in s for s in self._sent.get(thread_id, [])):
            return True
        if not self.mock_mode and self._page:
            return pw.thread_already_contains(self._page, text)
        return False

    def set_screen_state(self, state: BrowserScreenState) -> None:
        self._screen = state.value

    # ---------- mock (offline tests) ----------

    def _open_listing_mock(self, url: str, listing_id: str) -> str:
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

    def _send_message_mock(self, text: str, *, thread_id: str = "", listing_id: str = "") -> SendResult:
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

    # ---------- live Playwright ----------

    def _open_listing_live(self, url: str, listing_id: str) -> str:
        self.ensure_browser()
        page = self._page
        self._current_listing_url = url
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
        except Exception as exc:
            self._diag("open_listing_nav", error=str(exc))
            self._screen = BrowserScreenState.UNKNOWN.value
            return self._screen

        security = pw.detect_security_state(page.url, self._body_text())
        if security:
            self._screen = security
            return security

        if listing_id in self._listing_threads:
            thread_url = self._thread_urls.get(self._listing_threads[listing_id], "")
            if thread_url:
                page.goto(thread_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
            self._screen = BrowserScreenState.MESSENGER_OPEN.value
            if pw.find_composer(page):
                self._screen = BrowserScreenState.MESSAGE_INPUT_READY.value
            return self._screen

        if pw.find_message_button(page):
            self._screen = BrowserScreenState.MESSAGE_BUTTON_AVAILABLE.value
            return self._screen

        if pw.find_composer(page):
            self._screen = BrowserScreenState.MESSAGE_INPUT_READY.value
            return self._screen

        self._screen = BrowserScreenState.LISTING.value
        self._diag("open_listing_no_message_button")
        return self._screen

    def _send_message_live(self, text: str, *, thread_id: str = "", listing_id: str = "") -> SendResult:
        self.ensure_browser()
        page = self._page

        security = pw.detect_security_state(page.url, self._body_text())
        if security:
            return SendResult(False, error=security, screen_state=security)

        if thread_id and thread_id in self._thread_urls:
            try:
                page.goto(self._thread_urls[thread_id], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
            except Exception as exc:
                return SendResult(False, error=str(exc), screen_state=self._screen)

        elif self._screen == BrowserScreenState.MESSAGE_BUTTON_AVAILABLE.value:
            if not pw.click_message_button(page):
                self._diag("message_button_missing")
                return SendResult(False, error="message_button_not_found", screen_state=self._screen)
            page.wait_for_timeout(1000)

        if not pw.wait_for_composer(page):
            self._diag("composer_missing")
            return SendResult(False, error="composer_not_found", screen_state=self._screen)

        if not pw.type_and_send(page, text):
            self._diag("send_failed")
            return SendResult(False, error="send_failed", screen_state=self._screen)

        thread_url = page.url
        tid, thread_url = self.bind_thread(listing_id, thread_url)
        if thread_id:
            tid = thread_id
        self._sent.setdefault(tid, []).append(text)
        self._thread_urls[tid] = thread_url
        if listing_id:
            self._listing_threads[listing_id] = tid
        self._screen = BrowserScreenState.WAITING_REPLY.value
        return SendResult(True, thread_id=tid, thread_url=thread_url, screen_state=self._screen)

    def _poll_inbound_live(self, thread_id: str) -> list[InboundMessage]:
        self.ensure_browser()
        url = self._thread_urls.get(thread_id)
        if not url:
            return []
        page = self._page
        try:
            if url not in page.url:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
        except Exception:
            return []

        seen = self._last_seen.setdefault(thread_id, set())
        new_msgs: list[InboundMessage] = []
        for text in pw.read_thread_messages(page):
            if text in seen:
                continue
            if any(text[:40] in s for s in self._sent.get(thread_id, [])):
                seen.add(text)
                continue
            seen.add(text)
            new_msgs.append(InboundMessage(text=text, thread_id=thread_id))
        return new_msgs

    def _body_text(self) -> str:
        try:
            return self._page.inner_text("body", timeout=3000)
        except Exception:
            return ""

    def _detect_login_required(self, url: str) -> bool:
        return "login" in (url or "").lower()

    def _detect_checkpoint(self, url: str) -> bool:
        u = (url or "").lower()
        return "checkpoint" in u or "security" in u

    def _diag(self, name: str, **extra: str) -> None:
        html = ""
        url = ""
        try:
            if self._page:
                url = self._page.url
                html = self._page.content()
        except Exception:
            pass
        self.save_diagnostic(name, url=url, html=html, extra=extra)

    def save_diagnostic(
        self, name: str, *, url: str = "", html: str = "", extra: dict | None = None,
    ) -> None:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        path = DIAG_DIR / f"{name}_{int(time.time())}.txt"
        body = f"url={url}\nextra={extra or {}}\n\n{html[:8000]}"
        path.write_text(body, encoding="utf-8")
        try:
            if self._page:
                self._page.screenshot(path=str(DIAG_DIR / f"{name}_{int(time.time())}.png"))
        except Exception:
            pass

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
            viewport={"width": 1280, "height": 900},
            locale="ru-RU",
        )
        self._page = self._browser.pages[0] if self._browser.pages else self._browser.new_page()

    def stop_browser(self) -> None:
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._browser_started = False
