"""Offline WhatsApp Agent 6 parity tests — shared core, no network."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.messaging.conversation_key import (
    telegram_conversation_key,
    whatsapp_conversation_key,
    whatsapp_session_chat_id,
)
from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    apply_inbound_to_ownership,
    resume_bot,
    set_manager_takeover,
)
from agent6_qualifier.messaging.types import (
    CanonicalInboundMessage,
    ConversationOwner,
    OutboundTextRequest,
)
from agent6_qualifier.messaging.wazzup_client import WazzupClient, new_crm_message_id
from agent6_qualifier.messaging.wazzup_config import load_wazzup_config
from agent6_qualifier.messaging.wazzup_errors import WazzupSendDisabled
from agent6_qualifier.messaging.wazzup_transport import WazzupWhatsAppTransport
from agent6_qualifier.messaging.outbound_guard import send_text_guarded
from agent6_qualifier.messaging.wa_client_runtime import (
    agent7_live_outreach_enabled,
    process_whatsapp_client_turn,
)
from agent6_qualifier.messaging.webhook import process_wazzup_webhook
from agent6_qualifier.qualifier import Qualifier, Session, Turn
from agent6_qualifier.sessions import SessionStore


EXPECTED_CHANNEL = "7ac4092a-cf3c-450f-8518-d7cc7bd3f995"


@pytest.fixture
def wazzup_env(monkeypatch):
    monkeypatch.setenv("WAZZUP_API_KEY", "test-key-not-real")
    monkeypatch.setenv("WAZZUP_CHANNEL_ID", EXPECTED_CHANNEL)
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "false")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_PHONES", "+66625124001")
    monkeypatch.setenv("WAZZUP_STAGE_MODE", "true")
    monkeypatch.setenv("AGENT7_LIVE_OUTREACH_ENABLED", "false")
    monkeypatch.setenv("CONTACT_ROLE_AUTO_ASSIGN_ENABLED", "false")


def _msg(
    *,
    text: str = "Здравствуйте",
    phone: str = "+66625124001",
    message_id: str = "m1",
    direction: str = "inbound",
    crm_message_id: str | None = None,
    is_from_bot: bool | None = None,
) -> CanonicalInboundMessage:
    return CanonicalInboundMessage(
        provider="wazzup",
        channel="whatsapp",
        message_id=message_id,
        chat_id=phone.lstrip("+"),
        phone=phone.lstrip("+") if phone.startswith("+") else phone,
        direction=direction,
        text=text,
        timestamp=datetime.now(timezone.utc),
        channel_id=EXPECTED_CHANNEL,
        crm_message_id=crm_message_id,
        is_from_bot=is_from_bot,
    )


def test_conversation_keys_separated():
    assert telegram_conversation_key(123) == "telegram:123"
    assert whatsapp_conversation_key("+66625124001") == "whatsapp:+66625124001"
    assert whatsapp_session_chat_id("+66625124001") == "wa_66625124001"


@pytest.mark.asyncio
async def test_wa_uses_shared_process_and_persists(wazzup_env, tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    calls = {"pcm": 0}

    async def fake_pcm(**kwargs):
        calls["pcm"] += 1
        sess = kwargs["get_session"](kwargs["chat_id"])
        sess.asked_core = True
        sess.lead.budget = 80000
        await kwargs["send_client_response"](kwargs["event"], "Привет! Какие даты?")
        kwargs["store"].save(sess)

    monkeypatch.setattr(
        "agent6_qualifier.client_handler.process_client_message",
        fake_pcm,
    )
    # Avoid real Qualifier/Notion in this unit path — fake_pcm replaces core call.
    turn = await process_whatsapp_client_turn(
        _msg(text="Здравствуйте", message_id="t1"),
        store=store,
        force_dry_run_send=True,
        qualifier=Qualifier(find_by_id=lambda _: None, fetch_all=lambda: []),
    )
    assert calls["pcm"] == 1
    assert turn.processed is True
    assert turn.outbound_mode == "dry_run"
    loaded = store.load(whatsapp_session_chat_id("+66625124001"))
    assert loaded is not None
    assert loaded.asked_core is True
    assert loaded.lead.budget == 80000


@pytest.mark.asyncio
async def test_restart_persistence(wazzup_env, tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    chat = whatsapp_session_chat_id("+66625124001")
    s = Session(chat_id=chat)
    s.lead.source_channel = "whatsapp"
    s.asked_core = True
    s.amo_lead_id = 42
    store.save(s)

    # new runtime cache
    from agent6_qualifier.messaging import wa_client_runtime as rt

    rt._sessions_cache.clear()

    async def fake_pcm(**kwargs):
        sess = kwargs["get_session"](kwargs["chat_id"])
        assert sess.asked_core is True
        assert sess.amo_lead_id == 42
        await kwargs["send_client_response"](kwargs["event"], "ok")
        kwargs["store"].save(sess)

    monkeypatch.setattr(
        "agent6_qualifier.client_handler.process_client_message",
        fake_pcm,
    )
    turn = await process_whatsapp_client_turn(
        _msg(text="даты с 1 по 10", message_id="t2"),
        store=store,
        force_dry_run_send=True,
        qualifier=Qualifier(find_by_id=lambda _: None, fetch_all=lambda: []),
    )
    assert turn.processed is True


def test_allowlist_blocks_unknown(wazzup_env):
    cfg = load_wazzup_config()
    assert cfg.phone_allowed_for_live("+66625124001") is True
    assert cfg.phone_allowed_for_live("+66999999999") is False


def test_live_send_requires_flags(wazzup_env, tmp_path):
    cfg = load_wazzup_config()
    posts: list[str] = []

    def http(method, url, headers, body, timeout):
        posts.append(method)
        return 200, b'{"messageId":"x"}'

    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="66625124001")
    store = ProcessedEventStore(tmp_path / "e.sqlite")
    req = OutboundTextRequest(
        recipient_chat_id="+66625124001",
        text="hi",
        crm_message_id=new_crm_message_id(),
    )
    # dry-run path — no POST
    send_text_guarded(transport, req, ownership=ownership, store=store, dry_run=True)
    assert posts == []
    with pytest.raises(WazzupSendDisabled):
        transport.bot_may_send()


def test_allowlist_blocks_live_post(wazzup_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    cfg = load_wazzup_config()
    posts: list[str] = []

    def http(method, url, headers, body, timeout):
        posts.append(method)
        return 200, b"{}"

    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="x")
    with pytest.raises(WazzupSendDisabled):
        send_text_guarded(
            transport,
            OutboundTextRequest(
                recipient_chat_id="+66999999999",
                text="hi",
                crm_message_id=new_crm_message_id(),
            ),
            ownership=ownership,
            store=ProcessedEventStore(tmp_path / "e2.sqlite"),
            dry_run=False,
        )
    assert posts == []


def test_duplicate_inbound_no_second_process(wazzup_env, tmp_path):
    cfg = load_wazzup_config()
    store = ProcessedEventStore(tmp_path / "dup.sqlite")
    body = {
        "messages": [
            {
                "messageId": "same-id",
                "channelId": EXPECTED_CHANNEL,
                "chatId": "66625124001",
                "chatType": "whatsapp",
                "direction": "inbound",
                "text": "hi",
            }
        ]
    }
    r1 = process_wazzup_webhook(body, config=cfg, store=store, use_shared_core=False)
    r2 = process_wazzup_webhook(body, config=cfg, store=store, use_shared_core=False)
    assert len(r1.messages) == 1
    assert r2.duplicates == ["same-id"]


def test_bot_outbound_echo_ignored(wazzup_env):
    state = ConversationOwnershipState(chat_id="66625124001")
    echo = _msg(
        direction="outbound",
        crm_message_id="agent6-abc",
        is_from_bot=True,
        text="bot reply",
    )
    apply_inbound_to_ownership(state, echo)
    assert state.owner == ConversationOwner.BOT_ACTIVE


def test_human_outbound_handoff_blocks(wazzup_env):
    state = ConversationOwnershipState(chat_id="66625124001")
    human = _msg(
        direction="outbound",
        is_from_bot=False,
        text="manager says hi",
        message_id="h1",
    )
    apply_inbound_to_ownership(state, human)
    assert state.owner == ConversationOwner.HUMAN_HANDOFF
    set_manager_takeover(state, enabled=True)
    with pytest.raises(PermissionError):
        from agent6_qualifier.messaging.ownership import assert_bot_may_send

        assert_bot_may_send(state)
    resume_bot(state)
    assert state.owner == ConversationOwner.BOT_ACTIVE


def test_agent7_live_disabled_by_default(wazzup_env):
    assert agent7_live_outreach_enabled() is False


def test_crm_message_id_unique():
    a = new_crm_message_id()
    b = new_crm_message_id()
    assert a.startswith("agent6-")
    assert a != b


def test_webhook_serve_allows_send_flag_with_allowlist(wazzup_env, monkeypatch):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_ENABLED", "true")
    cfg = load_wazzup_config()
    assert cfg.send_enabled is True
    assert cfg.live_allowlist_enabled is True
    # serve script would refuse only if allowlist disabled
    assert not (cfg.send_enabled and cfg.auto_reply_enabled and not cfg.live_allowlist_enabled)
