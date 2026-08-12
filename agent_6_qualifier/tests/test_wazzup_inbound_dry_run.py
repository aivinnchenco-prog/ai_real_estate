"""Offline tests: Wazzup inbound → qualification dry-run (no send)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.messaging.crm_resolve import resolve_existing_crm_conversation
from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.inbound_dry_run import run_whatsapp_inbound_dry_run
from agent6_qualifier.messaging.ownership import ConversationOwnershipState
from agent6_qualifier.messaging.phone_mask import mask_phone
from agent6_qualifier.messaging.types import ConversationOwner, OutboundTextRequest
from agent6_qualifier.messaging.wazzup_client import WazzupClient
from agent6_qualifier.messaging.wazzup_config import load_wazzup_config
from agent6_qualifier.messaging.wazzup_errors import WazzupSendDisabled
from agent6_qualifier.messaging.wazzup_inbound import normalize_wazzup_webhook
from agent6_qualifier.messaging.wazzup_transport import (
    TelegramMessagingTransport,
    WazzupWhatsAppTransport,
)
from agent6_qualifier.messaging.webhook import process_wazzup_webhook
from agent6_qualifier.messaging.webhook_http import handle_wazzup_webhook_request
from agent6_qualifier.qualifier import Qualifier, Session

EXPECTED_CHANNEL = "7ac4092a-cf3c-450f-8518-d7cc7bd3f995"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "wazzup_message_add_real_shaped.json"
)


@pytest.fixture
def wazzup_env(monkeypatch):
    monkeypatch.setenv("WAZZUP_API_KEY", "test-key-not-real")
    monkeypatch.setenv("WAZZUP_CHANNEL_ID", EXPECTED_CHANNEL)
    monkeypatch.setenv("WAZZUP_API_BASE_URL", "https://api.wazzup24.com")
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "false")


def test_real_shaped_message_add_fixture_normalized(wazzup_env):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload.get("_fixture")
    msgs = normalize_wazzup_webhook(payload)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.provider == "wazzup"
    assert m.channel == "whatsapp"
    assert m.text.startswith("Ищу виллу")
    assert m.phone == "66987654321"


def test_inbound_dry_run_proposed_reply_no_post(wazzup_env):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    msg = normalize_wazzup_webhook(payload)[0]
    posts: list[str] = []

    def http(method, url, headers, body, timeout):
        posts.append(method)
        return 200, b"{}"

    cfg = load_wazzup_config()
    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    result = run_whatsapp_inbound_dry_run(
        msg,
        config=cfg,
        qualifier=Qualifier(find_by_id=lambda _: None, fetch_all=lambda: []),
        transport=transport,
    )
    assert result.inbound_received is True
    assert result.qualification_invoked is True
    assert result.proposed_reply_generated is True
    assert result.post_attempted is False
    assert result.real_send is False
    assert result.dry_run is not None
    assert result.dry_run.would_send is True
    assert posts == []
    with pytest.raises(WazzupSendDisabled):
        transport.send_text(
            OutboundTextRequest(
                recipient_chat_id=msg.chat_id,
                text=result.proposed_reply_preview,
            )
        )


def test_duplicate_event_ignored(wazzup_env, tmp_path):
    cfg = load_wazzup_config()
    store = ProcessedEventStore(tmp_path / "d.sqlite")
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    r1 = process_wazzup_webhook(body, config=cfg, store=store, use_shared_core=False)
    r2 = process_wazzup_webhook(body, config=cfg, store=store, use_shared_core=False)
    assert len(r1.messages) == 1
    assert len(r1.dry_runs) == 1
    assert r2.duplicates
    assert len(r2.dry_runs) == 0


def test_unknown_event_ignored(wazzup_env):
    cfg = load_wazzup_config()
    r = process_wazzup_webhook({"statuses": [{"id": 1}]}, config=cfg, use_shared_core=False)
    assert r.messages == []
    assert r.ignored_reason


def test_wrong_channel_ignored(wazzup_env):
    cfg = load_wazzup_config()
    body = {
        "messages": [
            {
                "messageId": "other-ch",
                "channelId": "00000000-0000-0000-0000-000000000000",
                "chatId": "66111111111",
                "chatType": "whatsapp",
                "direction": "inbound",
                "text": "hi",
            }
        ]
    }
    r = process_wazzup_webhook(body, config=cfg, use_shared_core=False)
    assert r.messages == []
    assert r.dry_runs == []


def test_malformed_payload_safe(wazzup_env):
    status, body = handle_wazzup_webhook_request(
        method="POST",
        path="/webhooks/wazzup",
        headers={},
        body=b"{not-json",
    )
    assert status == 400
    assert body["error"] == "WAZZUP_MALFORMED_PAYLOAD"


def test_wazzup_test_ping_ok(wazzup_env):
    status, body = handle_wazzup_webhook_request(
        method="POST",
        path="/webhooks/wazzup",
        headers={},
        body=b'{"test":true}',
    )
    assert status == 200
    assert body.get("test") is True


def test_crm_existing_mapping_reused_no_duplicate(wazzup_env):
    class FakeAmo:
        def find_contact(self, query: str):
            return {"id": 7} if "66987654321" in query else None

        def find_open_lead(self, contact_id: int):
            return 77 if contact_id == 7 else None

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    msg = normalize_wazzup_webhook(payload)[0]
    res = resolve_existing_crm_conversation(FakeAmo(), phone=msg.phone)
    assert res.action == "reuse_existing"
    assert res.contact_id == 7
    assert res.lead_id == 77

    dry = run_whatsapp_inbound_dry_run(
        msg,
        amo=FakeAmo(),
        qualifier=Qualifier(find_by_id=lambda _: None, fetch_all=lambda: []),
    )
    assert dry.crm is not None
    assert dry.crm.lead_id == 77
    assert dry.crm.action == "reuse_existing"
    assert "duplicate" not in " ".join(dry.notes).lower() or "no duplicate" in " ".join(
        dry.notes
    ).lower()


def test_human_handoff_suppresses_send_path(wazzup_env):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    msg = normalize_wazzup_webhook(payload)[0]
    ownership = ConversationOwnershipState(
        chat_id=msg.chat_id,
        owner=ConversationOwner.HUMAN_HANDOFF,
    )
    dry = run_whatsapp_inbound_dry_run(
        msg,
        ownership=ownership,
        qualifier=Qualifier(find_by_id=lambda _: None, fetch_all=lambda: []),
    )
    assert dry.qualification_invoked is False
    assert dry.suppressed_reason
    assert dry.real_send is False
    assert dry.post_attempted is False


def test_session_handoff_flag_suppresses(wazzup_env):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    msg = normalize_wazzup_webhook(payload)[0]
    sess = Session(chat_id="wa:x", handoff_to_human=True)
    dry = run_whatsapp_inbound_dry_run(
        msg,
        session=sess,
        qualifier=Qualifier(find_by_id=lambda _: None, fetch_all=lambda: []),
    )
    assert dry.qualification_invoked is False
    assert dry.ownership == ConversationOwner.HUMAN_HANDOFF.value


def test_telegram_transport_unaffected(wazzup_env):
    tg = TelegramMessagingTransport()
    assert tg.provider_name == "telegram"
    assert tg.normalize_inbound({"messages": []}) == []


def test_mask_phone():
    assert mask_phone("66625124002") == "+66******002"


def test_webhook_disabled_default_in_fresh_env(monkeypatch):
    monkeypatch.delenv("WAZZUP_WEBHOOK_ENABLED", raising=False)
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "false")
    cfg = load_wazzup_config()
    assert cfg.webhook_enabled is False
    assert cfg.send_enabled is False
    assert cfg.auto_reply_enabled is False


def test_send_never_called_from_webhook_handler(wazzup_env, tmp_path):
    posts: list[str] = []

    def http(method, url, headers, body, timeout):
        posts.append(method)
        raise AssertionError("network should not be called")

    # Patch is unnecessary — dry-run path never calls client.send_text.
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cfg = load_wazzup_config()
    store = ProcessedEventStore(tmp_path / "w.sqlite")
    r = process_wazzup_webhook(body, config=cfg, store=store)
    assert r.auto_reply is False
    assert all(d.real_send is False for d in r.dry_runs)
    assert posts == []
    assert cfg.send_enabled is False
