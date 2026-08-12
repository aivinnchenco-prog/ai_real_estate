"""Tests for WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF live-reply guard.

Offline only — no real WhatsApp / Wazzup PATCH / amo / Notion.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.outbound_guard import (
    DuplicateOutboundSuppressed,
    send_text_guarded,
)
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    apply_inbound_to_ownership,
    assert_bot_may_send,
    classify_outbound_source,
)
from agent6_qualifier.messaging.types import (
    CanonicalInboundMessage,
    ConversationOwner,
    OutboundTextRequest,
)
from agent6_qualifier.messaging.wazzup_client import WazzupClient, new_crm_message_id
from agent6_qualifier.messaging.wazzup_config import load_wazzup_config
from agent6_qualifier.messaging.wazzup_errors import (
    WazzupAutoReplyDisabled,
    WazzupExternalAutoresponseNotConfirmed,
    WazzupSendDisabled,
)
from agent6_qualifier.messaging.wazzup_transport import (
    TelegramMessagingTransport,
    WazzupWhatsAppTransport,
)
from agent6_qualifier.messaging.webhook_http import handle_wazzup_webhook_request


EXPECTED_CHANNEL = "7ac4092a-cf3c-450f-8518-d7cc7bd3f995"
FAKE_KEY = "test-wazzup-key-not-real-secret"
ALLOW_PHONE = "+66625124001"


@pytest.fixture
def wazzup_env(monkeypatch):
    monkeypatch.setenv("WAZZUP_API_KEY", FAKE_KEY)
    monkeypatch.setenv("WAZZUP_CHANNEL_ID", EXPECTED_CHANNEL)
    monkeypatch.setenv("WAZZUP_API_BASE_URL", "https://api.wazzup24.com")
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_WEBHOOK_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "false")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_PHONES", ALLOW_PHONE)
    monkeypatch.setenv("WAZZUP_STAGE_MODE", "true")


def _transport(cfg, posts: list):
    def http(method, url, headers, body, timeout):
        posts.append(method)
        assert "Authorization" in headers
        # never leak into assertions as printed secret — just ensure present
        return 200, b'{"messageId":"ok"}'

    return WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))


def _req(phone: str = ALLOW_PHONE, crm: str | None = None) -> OutboundTextRequest:
    return OutboundTextRequest(
        recipient_chat_id=phone,
        text="hi",
        crm_message_id=crm or new_crm_message_id(),
    )


def test_01_safe_defaults_false(wazzup_env):
    cfg = load_wazzup_config()
    assert cfg.send_enabled is False
    assert cfg.auto_reply_enabled is False
    assert cfg.external_autoresponse_confirmed_off is False


def test_02_send_blocked_if_external_autoresponse_not_confirmed(
    wazzup_env, tmp_path, monkeypatch
):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "false")
    cfg = load_wazzup_config()
    posts: list[str] = []
    transport = _transport(cfg, posts)
    with pytest.raises(WazzupExternalAutoresponseNotConfirmed) as exc:
        send_text_guarded(
            transport,
            _req(),
            ownership=ConversationOwnershipState(chat_id="66625124001"),
            store=ProcessedEventStore(tmp_path / "a.sqlite"),
            dry_run=False,
        )
    assert "EXTERNAL_AUTORESPONSE_NOT_CONFIRMED_OFF" in str(exc.value)
    assert posts == []


def test_03_send_allowed_by_this_guard_when_confirmed(
    wazzup_env, tmp_path, monkeypatch
):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "true")
    cfg = load_wazzup_config()
    posts: list[str] = []
    transport = _transport(cfg, posts)
    transport.bot_may_send()  # must not raise on this guard
    payload = send_text_guarded(
        transport,
        _req(),
        ownership=ConversationOwnershipState(chat_id="66625124001"),
        store=ProcessedEventStore(tmp_path / "b.sqlite"),
        dry_run=False,
    )
    assert posts == ["POST"]
    assert isinstance(payload, dict)


def test_04_send_still_blocked_if_send_false(wazzup_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "true")
    cfg = load_wazzup_config()
    posts: list[str] = []
    with pytest.raises(WazzupSendDisabled):
        send_text_guarded(
            _transport(cfg, posts),
            _req(),
            ownership=ConversationOwnershipState(chat_id="66625124001"),
            store=ProcessedEventStore(tmp_path / "c.sqlite"),
        )
    assert posts == []


def test_05_send_still_blocked_if_auto_reply_false(
    wazzup_env, tmp_path, monkeypatch
):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "true")
    cfg = load_wazzup_config()
    posts: list[str] = []
    with pytest.raises(WazzupAutoReplyDisabled):
        send_text_guarded(
            _transport(cfg, posts),
            _req(),
            ownership=ConversationOwnershipState(chat_id="66625124001"),
            store=ProcessedEventStore(tmp_path / "d.sqlite"),
        )
    assert posts == []


def test_06_non_allowlisted_phone_blocked(wazzup_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "true")
    cfg = load_wazzup_config()
    posts: list[str] = []
    with pytest.raises(WazzupSendDisabled) as exc:
        send_text_guarded(
            _transport(cfg, posts),
            _req(phone="+66999999999"),
            ownership=ConversationOwnershipState(chat_id="x"),
            store=ProcessedEventStore(tmp_path / "e.sqlite"),
        )
    assert "ALLOWLIST" in str(exc.value)
    assert posts == []


def test_07_human_handoff_blocked(wazzup_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "true")
    cfg = load_wazzup_config()
    posts: list[str] = []
    ownership = ConversationOwnershipState(
        chat_id="66625124001", owner=ConversationOwner.HUMAN_HANDOFF
    )
    with pytest.raises(PermissionError):
        send_text_guarded(
            _transport(cfg, posts),
            _req(),
            ownership=ownership,
            store=ProcessedEventStore(tmp_path / "f.sqlite"),
        )
    assert posts == []


def test_08_duplicate_blocked(wazzup_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "true")
    cfg = load_wazzup_config()
    posts: list[str] = []
    store = ProcessedEventStore(tmp_path / "g.sqlite")
    store.mark_outbound("agent6-dup", chat_id="66625124001", created_at="t")
    with pytest.raises(DuplicateOutboundSuppressed):
        send_text_guarded(
            _transport(cfg, posts),
            _req(crm="agent6-dup"),
            ownership=ConversationOwnershipState(chat_id="66625124001"),
            store=store,
        )
    assert posts == []


def test_09_own_outbound_echo_ignored(wazzup_env):
    state = ConversationOwnershipState(chat_id="66625124001")
    echo = CanonicalInboundMessage(
        provider="wazzup",
        channel="whatsapp",
        message_id="echo-1",
        chat_id="66625124001",
        phone="66625124001",
        direction="outbound",
        text="bot",
        timestamp=None,
        crm_message_id="agent6-xyz",
        is_from_bot=True,
    )
    assert classify_outbound_source(echo) == "OWN_BOT_OUTBOUND"
    apply_inbound_to_ownership(state, echo)
    assert state.owner == ConversationOwner.BOT_ACTIVE
    assert_bot_may_send(state)


def test_10_unknown_external_outbound_classified(wazzup_env):
    msg = CanonicalInboundMessage(
        provider="wazzup",
        channel="whatsapp",
        message_id="ext-1",
        chat_id="66625124001",
        phone="66625124001",
        direction="outbound",
        text="Hello! Thank you for contacting us. We'll get back to you soon.",
        timestamp=None,
        crm_message_id=None,
        is_from_bot=None,
    )
    assert classify_outbound_source(msg) == "UNKNOWN_EXTERNAL_OUTBOUND"
    state = ConversationOwnershipState(chat_id="66625124001")
    apply_inbound_to_ownership(state, msg)
    assert state.owner == ConversationOwner.BOT_ACTIVE
    assert "UNKNOWN_EXTERNAL_OUTBOUND" in state.reason


def test_11_diagnostic_never_prints_api_key(wazzup_env, monkeypatch):
    monkeypatch.setenv("WAZZUP_API_KEY", FAKE_KEY)
    # Mock GET so diagnose does not need network; inject via monkeypatch of client.
    from agent6_qualifier.messaging import wazzup_client as wc

    def fake_request(self, method, path, *, json_body=None, allow_retry=None):
        assert method.upper() == "GET"
        if path.endswith("/channels"):
            return [
                {
                    "channelId": EXPECTED_CHANNEL,
                    "transport": "whatsapp",
                    "state": "active",
                    "plainId": "66625124002",
                }
            ]
        if path.endswith("/webhooks"):
            return {"webhooksUri": "https://example.invalid/webhooks/wazzup"}
        return {}

    monkeypatch.setattr(wc.WazzupClient, "_request", fake_request)
    monkeypatch.setenv("WAZZUP_LOCAL_WEBHOOK_URL", "http://127.0.0.1:9/health")
    monkeypatch.delenv("WAZZUP_PUBLIC_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("PUBLIC_WEBHOOK_URL", raising=False)

    script = Path(__file__).resolve().parents[1] / "scripts" / "wazzup_live_reply_readiness.py"
    ns: dict = {"__name__": "__not_main__", "__file__": str(script)}
    code = compile(script.read_text(encoding="utf-8"), str(script), "exec")
    exec(code, ns)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ns["main"]()
    out = buf.getvalue()
    assert FAKE_KEY not in out
    assert "Authorization" not in out
    assert "323bd96e" not in out
    assert "EXTERNAL_AUTORESPONSE_CONFIRMED_OFF" in out
    assert "BEFORE LIVE ARM, USER MUST VERIFY" in out
    assert rc in (0, 1)


def test_12_diagnostic_masks_phones(wazzup_env, monkeypatch):
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_PHONES", "+66625124001")
    from agent6_qualifier.messaging import wazzup_client as wc

    monkeypatch.setattr(
        wc.WazzupClient,
        "_request",
        lambda self, method, path, **kw: (
            [
                {
                    "channelId": EXPECTED_CHANNEL,
                    "transport": "whatsapp",
                    "state": "active",
                    "plainId": "66625124002",
                }
            ]
            if path.endswith("/channels")
            else {"webhooksUri": "https://example.invalid/h"}
        ),
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "wazzup_live_reply_readiness.py"
    ns: dict = {"__name__": "__not_main__", "__file__": str(script)}
    exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), ns)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ns["main"]()
    out = buf.getvalue()
    assert "+66625124001" not in out
    assert "66625124001" not in out
    assert "***4001" in out or "Allowlisted phones count: 1" in out


def test_13_telegram_unaffected(wazzup_env):
    tg = TelegramMessagingTransport()
    assert tg.provider_name == "telegram"
    with pytest.raises(RuntimeError):
        tg.send_text(OutboundTextRequest(recipient_chat_id="1", text="x"))


def test_14_health_endpoint_safe(wazzup_env):
    status, body = handle_wazzup_webhook_request(
        method="GET",
        path="/health",
        headers={},
        body=b"",
    )
    assert status == 200
    assert body.get("health") is True
    assert body.get("ok") is True
