"""Telegram owner transport adapter (Telethon-backed, Agent7)."""

from __future__ import annotations

from agent7_envoy.messaging.base import (
    AuthStatus,
    HealthcheckResult,
    SendOutcome,
    SendTextRequest,
    SendTextResult,
    agent7_live_enabled,
)


class TelegramOwnerTransport:
    channel_name = "telegram"

    def __init__(self, send_fn=None):
        self._send_fn = send_fn

    def is_ready(self) -> bool:
        return True

    def healthcheck(self) -> HealthcheckResult:
        live = agent7_live_enabled()
        return HealthcheckResult(
            channel=self.channel_name,
            status=AuthStatus.READY,
            live_global=live,
            channel_enabled=True,
            ready=True,
            detail="telethon_adapter",
        )

    def send_text(self, request: SendTextRequest) -> SendTextResult:
        text = (request.text or "").strip()
        dest = (request.destination or "").strip()
        if not text or not dest:
            return SendTextResult(
                outcome=SendOutcome.BLOCKED,
                channel=self.channel_name,
                dry_run=request.dry_run,
                blocker="missing_text_or_destination",
                prepared_text=text,
            )
        if request.dry_run or not agent7_live_enabled():
            return SendTextResult(
                outcome=SendOutcome.DRY_RUN_READY
                if request.dry_run
                else SendOutcome.LIVE_OFF,
                channel=self.channel_name,
                dry_run=True,
                prepared_text=text,
                blocker="" if request.dry_run else "AGENT7_LIVE_OUTREACH_ENABLED=false",
            )
        if self._send_fn is None:
            return SendTextResult(
                outcome=SendOutcome.BLOCKED,
                channel=self.channel_name,
                dry_run=False,
                blocker="no_send_fn",
                prepared_text=text,
            )
        try:
            self._send_fn(dest, text)
        except Exception as exc:
            return SendTextResult(
                outcome=SendOutcome.UNKNOWN_SEND_STATE,
                channel=self.channel_name,
                dry_run=False,
                blocker=repr(exc),
                prepared_text=text,
            )
        return SendTextResult(
            outcome=SendOutcome.SENT,
            channel=self.channel_name,
            dry_run=False,
            prepared_text=text,
        )
