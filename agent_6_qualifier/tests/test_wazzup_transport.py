"""Offline tests for Agent 6 Wazzup WhatsApp transport.

No real network. Never asserts on raw API key values in logs.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.messaging.crm_resolve import resolve_existing_crm_conversation
from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.outbound_guard import (
    DuplicateOutboundSuppressed,
    prepare_outbound_request,
    send_text_guarded,
)
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    apply_inbound_to_ownership,
    assert_bot_may_send,
    set_manager_takeover,
)
from agent6_qualifier.messaging.types import (
    CanonicalInboundMessage,
    ConversationOwner,
    OutboundTextRequest,
)
from agent6_qualifier.messaging.wazzup_client import (
    WazzupClient,
    new_crm_message_id,
    normalize_channel,
)
from agent6_qualifier.messaging.wazzup_config import (
    DEFAULT_WAZZUP_CHANNEL_ID,
    load_wazzup_config,
    normalize_phone_e164_digits,
)
from agent6_qualifier.messaging.wazzup_errors import (
    WazzupExternalAutoresponseNotConfirmed,
    WazzupMalformedPayload,
    WazzupSendDisabled,
    WazzupTokenMissing,
    WazzupWebhookDisabled,
)
from agent6_qualifier.messaging.wazzup_inbound import normalize_wazzup_webhook
from agent6_qualifier.messaging.wazzup_transport import (
    TelegramMessagingTransport,
    WazzupWhatsAppTransport,
)
from agent6_qualifier.messaging.webhook import process_wazzup_webhook
from agent6_qualifier.messaging.webhook_http import handle_wazzup_webhook_request


EXPECTED_CHANNEL = "7ac4092a-cf3c-450f-8518-d7cc7bd3f995"
FAKE_KEY = "test-wazzup-key-not-real"


@pytest.fixture
def wazzup_env(monkeypatch):
    monkeypatch.setenv("WAZZUP_API_KEY", FAKE_KEY)
    monkeypatch.setenv("WAZZUP_CHANNEL_ID", EXPECTED_CHANNEL)
    monkeypatch.setenv("WAZZUP_API_BASE_URL", "https://api.wazzup24.com")
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_WEBHOOK_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "false")
    monkeypatch.setenv("WAZZUP_REQUEST_TIMEOUT_SECONDS", "15")


def _channels_payload():
    return [
        {
            "channelId": EXPECTED_CHANNEL,
            "transport": "whatsapp",
            "state": "active",
            "plainId": "66625124002",
            "name": "Open Home WA",
        },
        {
            "channelId": "other-channel",
            "transport": "telegram",
            "state": "active",
            "plainId": "1",
        },
    ]


def test_01_wazzup_config_loads(wazzup_env):
    cfg = load_wazzup_config()
    assert cfg.channel_id == EXPECTED_CHANNEL
    assert cfg.api_base_url == "https://api.wazzup24.com"
    assert cfg.api_key_set is True
    assert cfg.send_enabled is False
    assert cfg.webhook_enabled is False
    assert cfg.auto_reply_enabled is False
    assert cfg.external_autoresponse_confirmed_off is False


def test_02_missing_api_key_handled(monkeypatch):
    monkeypatch.delenv("WAZZUP_API_KEY", raising=False)
    monkeypatch.setenv("WAZZUP_CHANNEL_ID", EXPECTED_CHANNEL)
    cfg = load_wazzup_config()
    assert cfg.api_key_set is False
    client = WazzupClient(cfg, transport=lambda *a, **k: (200, b"[]"))
    with pytest.raises(WazzupTokenMissing) as exc:
        client.get_channels()
    assert "WAZZUP_TOKEN_MISSING" in str(exc.value)


def test_03_api_key_never_logged(wazzup_env, caplog):
    cfg = load_wazzup_config()
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers))
        return 200, json.dumps(_channels_payload()).encode()

    client = WazzupClient(cfg, transport=transport)
    with caplog.at_level(logging.DEBUG):
        client.get_channels()
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert FAKE_KEY not in joined
    assert "Authorization" not in joined
    assert all("Authorization" not in str(e) for e in client.request_log)


def test_04_get_channels_normalized(wazzup_env):
    cfg = load_wazzup_config()

    def transport(method, url, headers, body, timeout):
        assert method == "GET"
        assert url.endswith("/v3/channels")
        return 200, json.dumps(_channels_payload()).encode()

    client = WazzupClient(cfg, transport=transport)
    channels = [normalize_channel(c) for c in client.get_channels()]
    assert channels[0]["channel_id"] == EXPECTED_CHANNEL
    assert channels[0]["transport"] == "whatsapp"
    assert channels[0]["state"] == "active"
    assert channels[0]["plain_id"] == "66625124002"


def test_05_expected_channel_found(wazzup_env):
    cfg = load_wazzup_config()

    def transport(method, url, headers, body, timeout):
        return 200, json.dumps(_channels_payload()).encode()

    client = WazzupClient(cfg, transport=transport)
    ch = client.get_channel(EXPECTED_CHANNEL)
    assert ch is not None
    assert ch["plain_id"] == "66625124002"


def test_06_wrong_channel_blocked(wazzup_env):
    cfg = load_wazzup_config()
    transport = WazzupWhatsAppTransport(
        cfg, client=WazzupClient(cfg, transport=lambda *a, **k: (200, b"[]"))
    )
    with pytest.raises(Exception) as exc:
        transport.assert_expected_channel(
            {
                "channel_id": "wrong",
                "transport": "telegram",
                "state": "active",
                "plain_id": "1",
            }
        )
    assert "WAZZUP_CHANNEL_NOT_FOUND" in str(exc.value) or "wrong" in str(exc.value).lower()


def test_07_inactive_channel_warning_fail(wazzup_env):
    transport = WazzupWhatsAppTransport(load_wazzup_config())
    with pytest.raises(Exception) as exc:
        transport.assert_expected_channel(
            {
                "channel_id": EXPECTED_CHANNEL,
                "transport": "whatsapp",
                "state": "inactive",
                "plain_id": "66625124002",
            }
        )
    assert "inactive" in str(exc.value).lower() or "WAZZUP_CHANNEL_NOT_FOUND" in str(
        exc.value
    )


def test_08_send_false_blocks_post(wazzup_env):
    cfg = load_wazzup_config()
    assert cfg.send_enabled is False
    posts = []

    def transport(method, url, headers, body, timeout):
        posts.append(method)
        return 200, b"{}"

    client = WazzupClient(cfg, transport=transport)
    with pytest.raises(WazzupSendDisabled) as exc:
        client.send_text(
            chat_id="66625124002",
            text="hi",
            crm_message_id="agent6-test",
        )
    assert "WAZZUP_SEND_DISABLED" in str(exc.value)
    assert posts == []
    assert client.network_calls == 0


def test_09_dry_run_does_not_post(wazzup_env):
    cfg = load_wazzup_config()
    posts = []

    def http(method, url, headers, body, timeout):
        posts.append(method)
        return 200, b"{}"

    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="66625124002")
    req = prepare_outbound_request(recipient_chat_id="66625124002", text="hello")
    result = send_text_guarded(transport, req, ownership=ownership, dry_run=True)
    assert result.would_send is True
    assert result.provider == "wazzup"
    assert result.channel_id == EXPECTED_CHANNEL
    assert result.text_length == 5
    assert posts == []


def test_10_post_has_no_blind_retry(monkeypatch, wazzup_env):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    cfg = load_wazzup_config()
    attempts = []

    def http(method, url, headers, body, timeout):
        attempts.append(method)
        return 500, b'{"message":"boom"}'

    client = WazzupClient(cfg, transport=http)
    with pytest.raises(Exception) as exc:
        client.send_text(
            chat_id="66625124002",
            text="x",
            crm_message_id="agent6-retry-test",
        )
    assert "WAZZUP_SERVER_ERROR" in str(exc.value) or "500" in str(exc.value)
    assert attempts == ["POST"]
    assert client.network_calls == 1


def test_11_inbound_message_normalization():
    payload = {
        "messages": [
            {
                "messageId": "m1",
                "channelId": EXPECTED_CHANNEL,
                "chatId": "66625124002",
                "chatType": "whatsapp",
                "direction": "inbound",
                "text": "สวัสดี",
                "dateTime": "2026-08-09T10:00:00Z",
                "contact": {"name": "Ann"},
            }
        ]
    }
    msgs = normalize_wazzup_webhook(payload)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.provider == "wazzup"
    assert m.channel == "whatsapp"
    assert m.message_id == "m1"
    assert m.phone == "66625124002"
    assert m.text == "สวัสดี"
    assert m.sender_name == "Ann"
    assert m.channel_id == EXPECTED_CHANNEL
    assert "text" not in m.redacted_raw or "text_len" in m.redacted_raw


def test_12_malformed_webhook_safe():
    with pytest.raises(WazzupMalformedPayload):
        normalize_wazzup_webhook(b"not-json")
    with pytest.raises(WazzupMalformedPayload):
        normalize_wazzup_webhook([1, 2, 3])


def test_13_unsupported_event_ignored_safely():
    assert normalize_wazzup_webhook({"statuses": [{"messageId": "x"}]}) == []
    assert normalize_wazzup_webhook({"foo": "bar"}) == []


def test_14_duplicate_inbound_ignored(wazzup_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WAZZUP_WEBHOOK_ENABLED", "true")
    cfg = load_wazzup_config()
    store = ProcessedEventStore(tmp_path / "events.sqlite")
    body = {
        "messages": [
            {
                "messageId": "dup-1",
                "chatId": "66625124002",
                "chatType": "whatsapp",
                "direction": "inbound",
                "text": "hi",
            }
        ]
    }
    r1 = process_wazzup_webhook(body, config=cfg, store=store)
    r2 = process_wazzup_webhook(body, config=cfg, store=store)
    assert len(r1.messages) == 1
    assert len(r2.messages) == 0
    assert r2.duplicates == ["dup-1"]


def test_15_crm_message_id_idempotency_generated():
    a = new_crm_message_id()
    b = new_crm_message_id()
    assert a.startswith("agent6-")
    assert a != b
    req = prepare_outbound_request(recipient_chat_id="1", text="t")
    assert req.crm_message_id.startswith("agent6-")


def test_16_duplicate_outbound_suppressed(wazzup_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    cfg = load_wazzup_config()
    store = ProcessedEventStore(tmp_path / "out.sqlite")
    store.mark_outbound("agent6-same", chat_id="66625124002", created_at="t")
    transport = WazzupWhatsAppTransport(
        cfg, client=WazzupClient(cfg, transport=lambda *a, **k: (200, b"{}"))
    )
    ownership = ConversationOwnershipState(chat_id="66625124002")
    req = OutboundTextRequest(
        recipient_chat_id="66625124002",
        text="again",
        crm_message_id="agent6-same",
    )
    with pytest.raises(DuplicateOutboundSuppressed):
        send_text_guarded(transport, req, ownership=ownership, store=store)


@pytest.mark.parametrize(
    "owner",
    [
        ConversationOwner.HUMAN_HANDOFF,
        ConversationOwner.PAUSED,
        ConversationOwner.CLOSED,
    ],
)
def test_17_18_19_ownership_blocks_bot_send(owner):
    state = ConversationOwnershipState(chat_id="x", owner=owner)
    with pytest.raises(PermissionError):
        assert_bot_may_send(state)


def test_20_telegram_transport_unaffected():
    tg = TelegramMessagingTransport()
    assert tg.provider_name == "telegram"
    health = tg.healthcheck()
    assert health["ok"] is True
    assert tg.normalize_inbound({}) == []
    with pytest.raises(RuntimeError):
        tg.send_text(OutboundTextRequest(recipient_chat_id="1", text="x"))


def test_21_amocrm_duplicate_creation_guard():
    class FakeAmo:
        def find_contact(self, query: str):
            if "66625124002" in query:
                return {"id": 42}
            return None

        def find_open_lead(self, contact_id: int):
            return 99 if contact_id == 42 else None

    res = resolve_existing_crm_conversation(FakeAmo(), phone="+66 62 512 4002")
    assert res.action == "reuse_existing"
    assert res.contact_id == 42
    assert res.lead_id == 99
    assert res.found is True


def test_22_phone_normalization_plus66():
    assert normalize_phone_e164_digits("+66 62 512 4002") == "66625124002"
    assert normalize_phone_e164_digits("0625124002") == "66625124002"
    assert normalize_phone_e164_digits("66625124002") == "66625124002"


def test_23_auto_reply_disabled_by_default(wazzup_env):
    assert load_wazzup_config().auto_reply_enabled is False


def test_23b_external_autoresponse_confirmed_off_by_default(wazzup_env, monkeypatch):
    assert load_wazzup_config().external_autoresponse_confirmed_off is False
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "false")
    live = WazzupWhatsAppTransport(load_wazzup_config())
    with pytest.raises(WazzupExternalAutoresponseNotConfirmed):
        live.bot_may_send()


def test_24_webhook_disabled_by_default(wazzup_env):
    cfg = load_wazzup_config()
    assert cfg.webhook_enabled is False
    with pytest.raises(WazzupWebhookDisabled):
        process_wazzup_webhook({"messages": []}, config=cfg)
    status, body = handle_wazzup_webhook_request(
        method="POST",
        path="/webhooks/wazzup",
        headers={},
        body=b'{"messages":[]}',
    )
    assert status == 503
    assert body["error"] == "WAZZUP_WEBHOOK_DISABLED"


def test_25_no_real_network_in_tests(wazzup_env):
    """Ensure client path used in tests never hits real URL without mock."""
    cfg = load_wazzup_config()
    called = {"n": 0}

    def boom(method, url, headers, body, timeout):
        called["n"] += 1
        assert "api.wazzup24.com" in url  # URL built, but transport is mocked
        return 200, b"[]"

    client = WazzupClient(cfg, transport=boom)
    client.get_channels()
    assert called["n"] == 1
    # Default urllib transport is NOT used when mock provided.
    assert client._transport is boom


def test_human_handoff_from_explicit_non_bot_outbound():
    state = ConversationOwnershipState(chat_id="66625124002")
    msg = CanonicalInboundMessage(
        provider="wazzup",
        channel="whatsapp",
        message_id="out-1",
        chat_id="66625124002",
        phone="66625124002",
        direction="outbound",
        text="manager reply",
        timestamp=None,
        is_from_bot=False,
    )
    apply_inbound_to_ownership(state, msg)
    assert state.owner == ConversationOwner.HUMAN_HANDOFF


def test_manager_takeover_flag():
    state = ConversationOwnershipState(chat_id="x")
    set_manager_takeover(state, enabled=True)
    assert state.owner == ConversationOwner.HUMAN_HANDOFF
    with pytest.raises(PermissionError):
        assert_bot_may_send(state)


def test_synthetic_message_add_fixture():
    payload = {
        "event": "message.add",
        "_fixture": "synthetic/mock",
        "data": {
            "message_id": "syn-1",
            "chat_id": "0625124002",
            "text": "hello",
            "direction": "inbound",
        },
    }
    msgs = normalize_wazzup_webhook(payload)
    assert len(msgs) == 1
    assert msgs[0].raw_event_type == "message.add"
    assert msgs[0].phone == "66625124002"


def test_default_channel_id_constant():
    assert DEFAULT_WAZZUP_CHANNEL_ID == EXPECTED_CHANNEL


def test_redacted_config_hides_key(wazzup_env):
    d = load_wazzup_config().redacted_dict()
    assert d["api_key"] == "SET"
    assert FAKE_KEY not in json.dumps(d)
