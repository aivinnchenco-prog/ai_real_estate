"""Tests for amo→Facebook send_to_source wiring."""

from __future__ import annotations

from agent7_envoy.amo_chat.source_send import (
    build_facebook_send_to_source,
    facebook_messenger_thread_url,
)
from agent7_envoy.messaging.base import SendOutcome, SendTextResult
from agent7_envoy.messaging.facebook_messenger import FacebookMessengerOwnerTransport


def test_thread_url_builder():
    assert facebook_messenger_thread_url("abc123") == (
        "https://www.facebook.com/messages/t/abc123"
    )
    assert facebook_messenger_thread_url("https://www.facebook.com/messages/t/x") == (
        "https://www.facebook.com/messages/t/x"
    )


def test_send_to_source_uses_thread_url_and_dry_run(monkeypatch):
    monkeypatch.setenv("AMO_CHAT_MANAGER_REPLY_LIVE", "false")
    captured = {}

    class FakeTransport:
        def send_text(self, request):
            captured["url"] = request.source_url
            captured["dry_run"] = request.dry_run
            captured["thread"] = request.expected_thread_id
            return SendTextResult(
                outcome=SendOutcome.DRY_RUN_READY,
                channel="facebook_messenger",
                dry_run=True,
                external_thread_id="abc123",
            )

    fn = build_facebook_send_to_source(FakeTransport(), dry_run=True)
    result = fn(
        channel="facebook",
        external_thread_id="abc123",
        text="manager hello",
        owner_request_id="orq-1",
        object_id="F_1",
        source_url="",
    )
    assert captured["url"] == "https://www.facebook.com/messages/t/abc123"
    assert captured["thread"] == "abc123"
    assert captured["dry_run"] is True
    assert result.outcome == SendOutcome.DRY_RUN_READY.value


def test_send_to_source_prefers_listing_source_url_when_facebook_host():
    class RecTransport(FacebookMessengerOwnerTransport):
        def send_text(self, request):
            assert request.source_url == "https://www.facebook.com/marketplace/item/123"
            return SendTextResult(
                outcome=SendOutcome.DRY_RUN_READY,
                channel="facebook_messenger",
                dry_run=True,
            )

    fn = build_facebook_send_to_source(RecTransport(), dry_run=True)
    fn(
        channel="facebook",
        external_thread_id="abc123",
        text="hi",
        source_url="https://www.facebook.com/marketplace/item/123",
    )
