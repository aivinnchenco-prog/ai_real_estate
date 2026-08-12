"""Playwright Chromium session for WhatsApp Web (manual auth only)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .config import WhatsAppUiSyncConfig
from .selectors import SELECTORS


class BrowserSessionStatus(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    READY = "READY"
    FAILED = "FAILED"
    LINKED_DEVICE_CONFLICT = "LINKED_DEVICE_CONFLICT"


@dataclass
class SessionProbe:
    status: BrowserSessionStatus
    message: str = ""
    url: str = ""


class WhatsAppBrowserSession:
    """Persistent Chromium profile for WhatsApp Web.

    No stealth, no captcha solving, no auto-login.
    """

    def __init__(self, config: WhatsAppUiSyncConfig | None = None):
        self.config = config or WhatsAppUiSyncConfig.from_env()
        self._playwright: Any = None
        self._context: Any = None
        self._browser: Any = None  # only when connect_over_cdp
        self._page: Any = None
        self._attached_cdp: bool = False

    @property
    def profile_dir(self) -> Path:
        return self.config.profile_dir

    @property
    def page(self) -> Any:
        return self._page

    def browser_session_status(self) -> BrowserSessionStatus:
        if self._page is None:
            return BrowserSessionStatus.DISCONNECTED
        probe = self.probe_auth()
        return probe.status

    def start(self, *, headless: bool | None = None) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright not installed; pip install playwright && playwright install chromium"
            ) from exc

        self._playwright = sync_playwright().start()
        cdp = (self.config.cdp_url or "").strip()
        if cdp:
            # Attach to a normal Chrome started outside Playwright.
            # This avoids automation launch flags that change WhatsApp Web UI.
            self._browser = self._playwright.chromium.connect_over_cdp(cdp)
            self._attached_cdp = True
            contexts = self._browser.contexts
            self._context = contexts[0] if contexts else self._browser.new_context()
            pages = self._context.pages
            wa_page = None
            for p in pages:
                try:
                    if "web.whatsapp.com" in (p.url or ""):
                        wa_page = p
                        break
                except Exception:
                    continue
            self._page = wa_page or (pages[0] if pages else self._context.new_page())
            self._page.set_default_timeout(self.config.page_load_timeout_ms)
            return

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        use_headless = self.config.headless if headless is None else headless

        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": use_headless,
            "viewport": {"width": 1400, "height": 900},
            "locale": self.config.locale,
            "args": ["--disable-dev-shm-usage"],
            # Reduce Playwright's automation chrome (still not full stealth).
            "ignore_default_args": ["--enable-automation"],
        }
        channel = (self.config.browser_channel or "chrome").strip().lower()
        if channel and channel != "chromium":
            launch_kwargs["channel"] = channel
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
        except Exception as exc:
            if channel and channel != "chromium":
                print(
                    f"[whatsapp_ui] channel={channel} failed ({exc}); "
                    "falling back to bundled Chromium",
                    flush=True,
                )
                launch_kwargs.pop("channel", None)
                self._context = self._playwright.chromium.launch_persistent_context(
                    **launch_kwargs
                )
            else:
                raise
        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )
        self._page.set_default_timeout(self.config.page_load_timeout_ms)

    def stop(self) -> None:
        try:
            if self._attached_cdp:
                # Do NOT close the user's Chrome — only disconnect Playwright.
                if self._browser is not None:
                    try:
                        self._browser.close()
                    except Exception:
                        pass
            else:
                if self._context is not None:
                    self._context.close()
        finally:
            self._context = None
            self._browser = None
            self._page = None
            self._attached_cdp = False
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    def open_whatsapp(self) -> None:
        if self._page is None:
            raise RuntimeError("browser not started")
        self._page.goto(
            self.config.whatsapp_url,
            wait_until="domcontentloaded",
            timeout=self.config.page_load_timeout_ms,
        )
        # UI readiness wait only — not anti-detection jitter
        delay = max(0, self.config.action_delay_ms)
        if delay:
            self._page.wait_for_timeout(delay)
        probe = self.wait_until_settled()
        if probe.status == BrowserSessionStatus.READY:
            extra = max(0, self.config.ready_extra_wait_ms)
            if extra:
                self._page.wait_for_timeout(extra)

    def wait_until_settled(self, *, timeout_ms: int | None = None) -> SessionProbe:
        """Poll until READY / AUTH_REQUIRED / conflict, or timeout."""
        limit = timeout_ms if timeout_ms is not None else self.config.page_load_timeout_ms
        if self._page is None:
            return SessionProbe(BrowserSessionStatus.DISCONNECTED, "browser not started")
        deadline = time.time() + (limit / 1000.0)
        last = self.probe_auth()
        while time.time() < deadline:
            last = self.probe_auth()
            if last.status in {
                BrowserSessionStatus.READY,
                BrowserSessionStatus.AUTH_REQUIRED,
                BrowserSessionStatus.LINKED_DEVICE_CONFLICT,
            }:
                return last
            self._page.wait_for_timeout(1000)
        return last

    def probe_auth(self) -> SessionProbe:
        if self._page is None:
            return SessionProbe(BrowserSessionStatus.DISCONNECTED, "browser not started")
        try:
            url = self._page.url or ""
            body = ""
            try:
                body = (self._page.inner_text("body") or "")[:4000]
            except Exception:
                body = ""
            lower = body.lower()

            for hint in SELECTORS.linked_device_conflict_texts:
                if hint.lower() in lower:
                    return SessionProbe(
                        BrowserSessionStatus.LINKED_DEVICE_CONFLICT,
                        "linked device limit/conflict — stop; do not touch Wazzup",
                        url=url,
                    )

            # QR / login pane
            qr_found = False
            for name in SELECTORS.qr_accessible_names:
                try:
                    loc = self._page.get_by_role("img", name=name)
                    if loc.count() > 0:
                        qr_found = True
                        break
                except Exception:
                    continue
            if not qr_found:
                try:
                    # canvas QR is common on WA Web
                    if self._page.locator("canvas").count() > 0 and (
                        "qr" in lower or "scan" in lower or "link" in lower
                    ):
                        # Only treat as auth if chat search is absent
                        search_ok = self._search_box_present()
                        if not search_ok:
                            qr_found = True
                except Exception:
                    pass

            if qr_found and not self._search_box_present():
                return SessionProbe(
                    BrowserSessionStatus.AUTH_REQUIRED,
                    "QR / login required — run scripts/whatsapp_ui_login.py",
                    url=url,
                )

            if self._search_box_present() or self._chat_shell_present():
                return SessionProbe(
                    BrowserSessionStatus.READY,
                    "WhatsApp Web ready",
                    url=url,
                )

            return SessionProbe(
                BrowserSessionStatus.FAILED,
                "WhatsApp Web UI not ready / contract unconfirmed",
                url=url,
            )
        except Exception as exc:
            return SessionProbe(BrowserSessionStatus.FAILED, f"probe failed: {exc}")

    def _search_box_present(self) -> bool:
        assert self._page is not None
        # Prefer role=textbox / searchbox with accessible name / placeholder
        for role in ("searchbox", "textbox"):
            try:
                boxes = self._page.get_by_role(role)
                if boxes.count() > 0:
                    return True
            except Exception:
                continue
        for substr in SELECTORS.main_search_placeholder_substrings:
            try:
                loc = self._page.get_by_placeholder(substr)
                if loc.count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _chat_shell_present(self) -> bool:
        assert self._page is not None
        for hint in SELECTORS.chat_list_hints:
            try:
                if self._page.get_by_text(hint, exact=False).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def healthcheck(self) -> SessionProbe:
        if self._page is None:
            try:
                self.start()
                self.open_whatsapp()
                return self.probe_auth()
            except Exception as exc:
                return SessionProbe(BrowserSessionStatus.FAILED, str(exc))
            finally:
                # healthcheck may be used without keeping session; caller decides
                pass
        return self.probe_auth()
