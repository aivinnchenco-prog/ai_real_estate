"""Airbnb Messages owner transport via authenticated Playwright guest session.

Entry point = listing source_url → Contact host / inquiry thread.
Uses OutreachPlan text as-is. Conservative rate guard required.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from agent7_envoy.messaging.base import (
    AuthStatus,
    HealthcheckResult,
    SendOutcome,
    SendTextRequest,
    SendTextResult,
    TransportStageDiagnostics,
    agent7_live_enabled,
    airbnb_messages_enabled,
    browser_profiles_root,
)
from agent7_envoy.messaging.browser_common import (
    classify_auth_from_url,
    click_first_matching,
    find_composer,
    launch_persistent_context,
    save_failure_screenshot,
)
from agent7_envoy.messaging.rate_guard import AirbnbOwnerMessageRateGuard


LOGIN_URL_HINTS = (
    "login",
    "/authenticate",
    "two-factor",
    "2fa",
)

CONTACT_ROLES = (
    ("button", r"contact host"),
    ("link", r"contact host"),
    ("button", r"message host"),
    ("button", r"contact"),
    ("button", r"связ"),
)

CONTACT_TEXTS = (
    r"Contact host",
    r"Message host",
    r"Contact",
    r"Связаться",
    r"Написать хозяину",
)

CONTACT_SELECTORS = (
    '[data-testid*="contact" i]',
    'a[href*="/contact_host"]',
    'button[aria-label*="Contact" i]',
)

COMPOSER_PLACEHOLDERS = (
    r"start your message",
    r"write a message",
    r"message",
    r"сообщение",
)

COMPOSER_SELECTORS = (
    'textarea[id*="message" i]',
    'textarea[name*="message" i]',
    'div[role="textbox"][contenteditable="true"]',
    'textarea',
)


@dataclass
class AirbnbMessagesOwnerTransport:
    channel_name: str = "airbnb_messages"
    profile_dir: Path | None = None
    headless: bool = True
    rate_guard: AirbnbOwnerMessageRateGuard | None = None
    page_factory: Callable[[], Any] | None = None
    auth_probe: Callable[[], AuthStatus] | None = None
    navigator: Callable[..., dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.profile_dir is None:
            override = (os.getenv("AGENT7_AIRBNB_PROFILE_DIR") or "").strip()
            self.profile_dir = (
                Path(override).expanduser()
                if override
                else browser_profiles_root() / "airbnb_owner_outreach"
            )
        if self.rate_guard is None:
            self.rate_guard = AirbnbOwnerMessageRateGuard()

    def is_ready(self) -> bool:
        hc = self.healthcheck()
        return hc.ready and hc.status == AuthStatus.READY

    def healthcheck(self) -> HealthcheckResult:
        live = agent7_live_enabled()
        enabled = airbnb_messages_enabled()
        if not enabled:
            return HealthcheckResult(
                channel=self.channel_name,
                status=AuthStatus.DISABLED,
                live_global=live,
                channel_enabled=False,
                ready=False,
                detail="AGENT7_AIRBNB_MESSAGES_ENABLED=false",
                profile_dir=str(self.profile_dir),
            )
        assert self.rate_guard is not None
        rg = self.rate_guard.check()
        if not rg.allowed:
            return HealthcheckResult(
                channel=self.channel_name,
                status=AuthStatus.RATE_LIMITED,
                live_global=live,
                channel_enabled=enabled,
                ready=False,
                detail=rg.reason,
                profile_dir=str(self.profile_dir),
            )
        status = AuthStatus.AUTH_REQUIRED
        detail = "profile_missing"
        if self.auth_probe is not None:
            status = self.auth_probe()
            detail = status.value
        elif self.profile_dir and self.profile_dir.exists():
            marker = self.profile_dir / ".agent7_auth_ok"
            if marker.exists():
                status = AuthStatus.READY
                detail = "auth_marker_present"
            else:
                status = AuthStatus.AUTH_REQUIRED
                detail = "login_required_or_unconfirmed"
        return HealthcheckResult(
            channel=self.channel_name,
            status=status,
            live_global=live,
            channel_enabled=enabled,
            ready=status == AuthStatus.READY and enabled,
            detail=detail,
            profile_dir=str(self.profile_dir),
        )

    def mark_auth_ok(self) -> None:
        assert self.profile_dir is not None
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        (self.profile_dir / ".agent7_auth_ok").write_text("ok\n", encoding="utf-8")

    def clear_auth_marker(self) -> None:
        if self.profile_dir is None:
            return
        marker = self.profile_dir / ".agent7_auth_ok"
        if marker.exists():
            marker.unlink()

    def send_text(self, request: SendTextRequest) -> SendTextResult:
        diag = TransportStageDiagnostics()
        text = (request.text or "").strip()
        url = (request.source_url or request.destination or "").strip()

        if not airbnb_messages_enabled() and not request.dry_run:
            return SendTextResult(
                outcome=SendOutcome.CHANNEL_DISABLED,
                channel=self.channel_name,
                dry_run=False,
                blocker="AGENT7_AIRBNB_MESSAGES_ENABLED=false",
                prepared_text=text,
                diagnostics=diag,
            )
        if not request.dry_run and not agent7_live_enabled():
            return SendTextResult(
                outcome=SendOutcome.LIVE_OFF,
                channel=self.channel_name,
                dry_run=True,
                blocker="AGENT7_LIVE_OUTREACH_ENABLED=false",
                prepared_text=text,
                diagnostics=diag,
            )
        if not text:
            return SendTextResult(
                outcome=SendOutcome.BLOCKED,
                channel=self.channel_name,
                dry_run=request.dry_run,
                blocker="empty_message",
                diagnostics=diag,
            )

        assert self.rate_guard is not None
        rg = self.rate_guard.check()
        if not rg.allowed:
            return SendTextResult(
                outcome=SendOutcome.RATE_LIMITED,
                channel=self.channel_name,
                dry_run=request.dry_run,
                blocker="AIRBNB_RATE_LIMIT_GUARD",
                prepared_text=text,
                diagnostics=diag,
            )

        if not url or ("airbnb." not in url.lower() and self.navigator is None):
            return SendTextResult(
                outcome=SendOutcome.BLOCKED,
                channel=self.channel_name,
                dry_run=request.dry_run,
                blocker="invalid_airbnb_listing_url",
                prepared_text=text,
                diagnostics=diag,
            )

        if self.navigator is not None:
            return self._send_via_navigator(request, diag)

        hc = self.healthcheck()
        if hc.status in {AuthStatus.AUTH_REQUIRED, AuthStatus.SESSION_EXPIRED}:
            if self.page_factory is None:
                return SendTextResult(
                    outcome=SendOutcome.AUTH_REQUIRED,
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker=hc.detail or "AUTH_REQUIRED",
                    prepared_text=text,
                    diagnostics=diag,
                )

        return self._send_via_browser(request, diag)

    def _send_via_navigator(
        self, request: SendTextRequest, diag: TransportStageDiagnostics
    ) -> SendTextResult:
        assert self.navigator is not None
        nav = self.navigator(request)
        for stage in (
            "listing_opened",
            "message_action_found",
            "composer_found",
            "thread_identified",
        ):
            ok = bool(nav.get(stage))
            setattr(
                diag,
                {
                    "listing_opened": "listing_opened",
                    "message_action_found": "message_action_found",
                    "composer_found": "composer_found",
                    "thread_identified": "thread_identified",
                }[stage],
                ok,
            )
            diag.mark(stage.upper(), ok)
            if not ok:
                outcome_map = {
                    "listing_opened": SendOutcome.LISTING_NOT_OPENED,
                    "message_action_found": SendOutcome.MESSAGE_ACTION_NOT_FOUND,
                    "composer_found": SendOutcome.COMPOSER_NOT_FOUND,
                    "thread_identified": SendOutcome.THREAD_NOT_IDENTIFIED,
                }
                return SendTextResult(
                    outcome=outcome_map[stage],
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker=stage,
                    diagnostics=diag,
                    prepared_text=request.text,
                )
        if nav.get("unrelated_thread"):
            return SendTextResult(
                outcome=SendOutcome.CONTEXT_MISMATCH,
                channel=self.channel_name,
                dry_run=request.dry_run,
                blocker="unrelated_thread",
                diagnostics=diag,
                prepared_text=request.text,
            )
        thread_id = str(nav.get("thread_id") or "")
        expected = (request.expected_thread_id or "").strip()
        if expected and thread_id and expected != thread_id:
            return SendTextResult(
                outcome=SendOutcome.CONTEXT_MISMATCH,
                channel=self.channel_name,
                dry_run=request.dry_run,
                blocker="external_thread_mismatch",
                diagnostics=diag,
                prepared_text=request.text,
                external_thread_id=thread_id,
            )
        if request.dry_run or not agent7_live_enabled() or not airbnb_messages_enabled():
            return SendTextResult(
                outcome=SendOutcome.DRY_RUN_READY,
                channel=self.channel_name,
                dry_run=True,
                prepared_text=request.text,
                diagnostics=diag,
                external_thread_id=thread_id,
                external_conversation_url=str(nav.get("conversation_url") or ""),
            )
        assert self.rate_guard is not None
        self.rate_guard.record_send()
        return SendTextResult(
            outcome=SendOutcome.SENT,
            channel=self.channel_name,
            dry_run=False,
            prepared_text=request.text,
            diagnostics=diag,
            external_thread_id=thread_id,
            external_conversation_url=str(nav.get("conversation_url") or ""),
            external_message_id=str(nav.get("message_id") or ""),
        )

    def _send_via_browser(
        self, request: SendTextRequest, diag: TransportStageDiagnostics
    ) -> SendTextResult:
        url = (request.source_url or request.destination or "").strip()
        pw = context = page = None
        try:
            if self.page_factory is not None:
                page = self.page_factory()
            else:
                assert self.profile_dir is not None
                pw, context, page = launch_persistent_context(
                    profile_dir=self.profile_dir,
                    headless=self.headless,
                )
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            auth = classify_auth_from_url(page.url, login_hints=LOGIN_URL_HINTS)
            if auth == AuthStatus.AUTH_REQUIRED:
                self.clear_auth_marker()
                shot = save_failure_screenshot(page, "airbnb_auth")
                return SendTextResult(
                    outcome=SendOutcome.AUTH_REQUIRED,
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker="AUTH_REQUIRED",
                    diagnostics=diag,
                    screenshot_path=shot,
                    prepared_text=request.text,
                )
            diag.listing_opened = True
            diag.mark("LISTING_OPENED", True)

            clicked = click_first_matching(
                page,
                roles=CONTACT_ROLES,
                texts=CONTACT_TEXTS,
                selectors=CONTACT_SELECTORS,
            )
            if not clicked:
                diag.mark("MESSAGE_ACTION_FOUND", False)
                shot = save_failure_screenshot(page, "airbnb_no_contact")
                return SendTextResult(
                    outcome=SendOutcome.MESSAGE_ACTION_NOT_FOUND,
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker="MESSAGE_ACTION_FOUND",
                    diagnostics=diag,
                    screenshot_path=shot,
                    prepared_text=request.text,
                )
            diag.message_action_found = True
            diag.mark("MESSAGE_ACTION_FOUND", True)
            page.wait_for_timeout(1500)

            composer = find_composer(
                page,
                placeholders=COMPOSER_PLACEHOLDERS,
                selectors=COMPOSER_SELECTORS,
            )
            if composer is None:
                diag.mark("COMPOSER_FOUND", False)
                shot = save_failure_screenshot(page, "airbnb_no_composer")
                return SendTextResult(
                    outcome=SendOutcome.COMPOSER_NOT_FOUND,
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker="COMPOSER_FOUND",
                    diagnostics=diag,
                    screenshot_path=shot,
                    prepared_text=request.text,
                )
            diag.composer_found = True
            diag.mark("COMPOSER_FOUND", True)

            thread_id = self._infer_thread_id(page.url, listing_url=url)
            if not thread_id:
                diag.mark("THREAD_IDENTIFIED", False)
                shot = save_failure_screenshot(page, "airbnb_no_thread")
                return SendTextResult(
                    outcome=SendOutcome.THREAD_NOT_IDENTIFIED,
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker="THREAD_IDENTIFIED",
                    diagnostics=diag,
                    screenshot_path=shot,
                    prepared_text=request.text,
                )
            diag.thread_identified = True
            diag.mark("THREAD_IDENTIFIED", True)

            composer.click()
            composer.fill(request.text)

            if request.dry_run or not agent7_live_enabled() or not airbnb_messages_enabled():
                return SendTextResult(
                    outcome=SendOutcome.DRY_RUN_READY,
                    channel=self.channel_name,
                    dry_run=True,
                    prepared_text=request.text,
                    diagnostics=diag,
                    external_thread_id=thread_id,
                    external_conversation_url=page.url,
                )

            sent = click_first_matching(
                page,
                roles=(("button", r"^send$"), ("button", r"send message")),
                texts=(r"^Send$", r"Send message", r"Отправить"),
                selectors=('button[type="submit"]', '[data-testid*="send" i]'),
            )
            if not sent:
                shot = save_failure_screenshot(page, "airbnb_send_uncertain")
                return SendTextResult(
                    outcome=SendOutcome.UNKNOWN_SEND_STATE,
                    channel=self.channel_name,
                    dry_run=False,
                    blocker="send_click_failed",
                    diagnostics=diag,
                    screenshot_path=shot,
                    prepared_text=request.text,
                    external_thread_id=thread_id,
                )
            assert self.rate_guard is not None
            self.rate_guard.record_send()
            self.mark_auth_ok()
            return SendTextResult(
                outcome=SendOutcome.SENT,
                channel=self.channel_name,
                dry_run=False,
                prepared_text=request.text,
                diagnostics=diag,
                external_thread_id=thread_id,
                external_conversation_url=page.url,
            )
        except Exception as exc:
            shot = ""
            if page is not None:
                shot = save_failure_screenshot(page, "airbnb_exception")
            return SendTextResult(
                outcome=SendOutcome.BLOCKED,
                channel=self.channel_name,
                dry_run=request.dry_run,
                blocker=repr(exc),
                diagnostics=diag,
                screenshot_path=shot,
                prepared_text=request.text,
            )
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass

    @staticmethod
    def _infer_thread_id(url: str, *, listing_url: str = "") -> str:
        try:
            path = urlparse(url).path or ""
        except Exception:
            path = ""
        parts = [p for p in path.split("/") if p]
        for i, p in enumerate(parts):
            if p in {"thread", "threads", "inbox"} and i + 1 < len(parts):
                return parts[i + 1]
        # Fall back to listing room id for correlation until thread known
        try:
            lpath = urlparse(listing_url).path or ""
        except Exception:
            lpath = ""
        lparts = [p for p in lpath.split("/") if p]
        if "rooms" in lparts:
            idx = lparts.index("rooms")
            if idx + 1 < len(lparts):
                return f"listing:{lparts[idx + 1]}"
        if parts:
            return parts[-1]
        return ""
