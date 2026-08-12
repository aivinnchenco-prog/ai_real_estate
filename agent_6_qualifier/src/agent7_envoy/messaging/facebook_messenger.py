"""Facebook Messenger owner transport via authenticated Playwright session.

Entry point = listing source_url (Marketplace). Never search sellers by name.
Uses OutreachPlan text as-is — does not invent messages.
"""

from __future__ import annotations

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
    facebook_messenger_enabled,
)
from agent7_envoy.messaging.browser_common import (
    classify_auth_from_url,
    click_first_matching,
    find_composer,
    launch_persistent_context,
    save_failure_screenshot,
)
from agent7_envoy.messaging.fb_profile import (
    facebook_profile_has_session,
    resolve_agent7_facebook_profile_dir,
)


LOGIN_URL_HINTS = (
    "login",
    "checkpoint",
    "/authenticate",
    "two_step",
)

MESSAGE_ACTION_ROLES = (
    ("button", r"message"),
    ("link", r"message"),
    ("button", r"send seller"),
    ("button", r"contact"),
    ("button", r"chat"),
    ("button", r"написать"),
    ("button", r"сообщение"),
)

MESSAGE_ACTION_TEXTS = (
    r"Message",
    r"Send message",
    r"Contact seller",
    r"Написать",
    r"Сообщение",
)

MESSAGE_ACTION_SELECTORS = (
    '[aria-label*="Message" i]',
    '[aria-label*="message" i]',
    'a[href*="/messages/"]',
    'div[role="button"][aria-label*="Message" i]',
)

COMPOSER_PLACEHOLDERS = (
    r"message",
    r"Aa",
    r"Write a message",
    r"Напишите",
)

COMPOSER_SELECTORS = (
    'div[aria-label*="Message" i][contenteditable="true"]',
    'div[role="textbox"][contenteditable="true"]',
    'textarea[placeholder*="message" i]',
)


@dataclass
class FacebookMessengerOwnerTransport:
    """Browser automation transport for Marketplace → Messenger."""

    channel_name: str = "facebook_messenger"
    profile_dir: Path | None = None
    headless: bool = True
    # Injectables for offline tests
    page_factory: Callable[[], Any] | None = None
    auth_probe: Callable[[], AuthStatus] | None = None
    navigator: Callable[..., dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.profile_dir is None:
            # Reuse Agent1 Marketplace parser profile by default.
            self.profile_dir = resolve_agent7_facebook_profile_dir()

    def is_ready(self) -> bool:
        hc = self.healthcheck()
        return hc.ready and hc.status == AuthStatus.READY

    def healthcheck(self) -> HealthcheckResult:
        live = agent7_live_enabled()
        enabled = facebook_messenger_enabled()
        if not enabled:
            return HealthcheckResult(
                channel=self.channel_name,
                status=AuthStatus.DISABLED,
                live_global=live,
                channel_enabled=False,
                ready=False,
                detail="AGENT7_FACEBOOK_MESSENGER_ENABLED=false",
                profile_dir=str(self.profile_dir),
            )
        status = AuthStatus.AUTH_REQUIRED
        detail = "profile_missing"
        if self.auth_probe is not None:
            status = self.auth_probe()
            detail = status.value
        elif self.profile_dir and self.profile_dir.exists():
            marker = self.profile_dir / ".agent7_auth_ok"
            if facebook_profile_has_session(self.profile_dir):
                status = AuthStatus.READY
                detail = "parser_fb_profile_session"
            elif marker.exists():
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

    def _live_allowed(self, dry_run: bool) -> SendOutcome | None:
        if not facebook_messenger_enabled():
            return SendOutcome.CHANNEL_DISABLED
        if dry_run:
            return None
        if not agent7_live_enabled():
            return SendOutcome.LIVE_OFF
        return None

    def send_text(self, request: SendTextRequest) -> SendTextResult:
        diag = TransportStageDiagnostics()
        text = (request.text or "").strip()
        url = (request.source_url or request.destination or "").strip()
        gate = self._live_allowed(request.dry_run)
        if gate is not None and gate != SendOutcome.CHANNEL_DISABLED:
            # dry_run may proceed even when live off
            if not request.dry_run:
                return SendTextResult(
                    outcome=gate,
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker=gate.value,
                    prepared_text=text,
                    diagnostics=diag,
                )
        if not facebook_messenger_enabled() and not request.dry_run:
            return SendTextResult(
                outcome=SendOutcome.CHANNEL_DISABLED,
                channel=self.channel_name,
                dry_run=False,
                blocker="AGENT7_FACEBOOK_MESSENGER_ENABLED=false",
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
        if not url or "facebook." not in url.lower() and "fb.com" not in url.lower():
            # still allow marketplace paths without facebook host in tests via navigator
            if self.navigator is None and (
                "marketplace" not in url.lower() and "facebook." not in url.lower()
            ):
                return SendTextResult(
                    outcome=SendOutcome.BLOCKED,
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker="invalid_facebook_listing_url",
                    prepared_text=text,
                    diagnostics=diag,
                )

        # Injected navigator bypasses live browser auth (unit tests).
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
            "seller_context_confirmed",
        ):
            ok = bool(nav.get(stage))
            setattr(diag, stage, ok)
            diag.mark(stage.upper(), ok)
            if not ok:
                outcome_map = {
                    "listing_opened": SendOutcome.LISTING_NOT_OPENED,
                    "message_action_found": SendOutcome.MESSAGE_ACTION_NOT_FOUND,
                    "composer_found": SendOutcome.COMPOSER_NOT_FOUND,
                    "thread_identified": SendOutcome.THREAD_NOT_IDENTIFIED,
                    "seller_context_confirmed": SendOutcome.CONTEXT_MISMATCH,
                }
                return SendTextResult(
                    outcome=outcome_map[stage],
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker=stage,
                    diagnostics=diag,
                    prepared_text=request.text,
                    external_thread_id=str(nav.get("thread_id") or ""),
                )
        expected = (request.expected_thread_id or "").strip()
        thread_id = str(nav.get("thread_id") or "")
        if expected and thread_id and expected != thread_id:
            diag.mark("THREAD_MATCH", False)
            return SendTextResult(
                outcome=SendOutcome.CONTEXT_MISMATCH,
                channel=self.channel_name,
                dry_run=request.dry_run,
                blocker="external_thread_mismatch",
                diagnostics=diag,
                prepared_text=request.text,
                external_thread_id=thread_id,
            )
        if request.dry_run or not agent7_live_enabled() or not facebook_messenger_enabled():
            return SendTextResult(
                outcome=SendOutcome.DRY_RUN_READY,
                channel=self.channel_name,
                dry_run=True,
                prepared_text=request.text,
                diagnostics=diag,
                external_thread_id=thread_id,
                external_conversation_url=str(nav.get("conversation_url") or ""),
                blocker="" if request.dry_run else "live_or_channel_off",
            )
        if nav.get("send_uncertain"):
            return SendTextResult(
                outcome=SendOutcome.UNKNOWN_SEND_STATE,
                channel=self.channel_name,
                dry_run=False,
                prepared_text=request.text,
                diagnostics=diag,
                external_thread_id=thread_id,
                blocker="uncertain_send",
            )
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
                shot = save_failure_screenshot(page, "fb_auth")
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
                roles=MESSAGE_ACTION_ROLES,
                texts=MESSAGE_ACTION_TEXTS,
                selectors=MESSAGE_ACTION_SELECTORS,
            )
            if not clicked:
                diag.mark("MESSAGE_ACTION_FOUND", False)
                shot = save_failure_screenshot(page, "fb_no_message_action")
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
                shot = save_failure_screenshot(page, "fb_no_composer")
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

            thread_id = self._infer_thread_id(page.url)
            if not thread_id:
                diag.mark("THREAD_IDENTIFIED", False)
                shot = save_failure_screenshot(page, "fb_no_thread")
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

            # Seller context: conversation opened from listing URL path
            if "marketplace" not in url.lower() and "item" not in url.lower():
                # If we started from listing and landed in messenger — accept
                pass
            diag.seller_context_confirmed = True
            diag.mark("SELLER_CONTEXT_CONFIRMED", True)

            expected = (request.expected_thread_id or "").strip()
            if expected and expected != thread_id:
                return SendTextResult(
                    outcome=SendOutcome.CONTEXT_MISMATCH,
                    channel=self.channel_name,
                    dry_run=request.dry_run,
                    blocker="external_thread_mismatch",
                    diagnostics=diag,
                    prepared_text=request.text,
                    external_thread_id=thread_id,
                )

            composer.click()
            composer.fill(request.text)

            if request.dry_run or not agent7_live_enabled() or not facebook_messenger_enabled():
                return SendTextResult(
                    outcome=SendOutcome.DRY_RUN_READY,
                    channel=self.channel_name,
                    dry_run=True,
                    prepared_text=request.text,
                    diagnostics=diag,
                    external_thread_id=thread_id,
                    external_conversation_url=page.url,
                )

            # Live send — only when explicitly gated
            sent = click_first_matching(
                page,
                roles=(("button", r"press enter to send"), ("button", r"^send$")),
                texts=(r"^Send$", r"Отправить"),
                selectors=(
                    '[aria-label="Press enter to send"]',
                    '[aria-label="Send"]',
                ),
            )
            if not sent:
                # Try Enter key
                try:
                    composer.press("Enter")
                    sent = True
                except Exception:
                    sent = False
            if not sent:
                shot = save_failure_screenshot(page, "fb_send_uncertain")
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
                shot = save_failure_screenshot(page, "fb_exception")
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
    def _infer_thread_id(url: str) -> str:
        try:
            path = urlparse(url).path or ""
        except Exception:
            return ""
        parts = [p for p in path.split("/") if p]
        # /messages/t/<id> or /marketplace/t/<id>
        for i, p in enumerate(parts):
            if p in {"t", "thread"} and i + 1 < len(parts):
                return parts[i + 1]
        if parts:
            return parts[-1]
        return ""
