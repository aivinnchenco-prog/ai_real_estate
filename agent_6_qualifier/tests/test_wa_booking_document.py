"""Offline tests: Agent8 WhatsApp booking DOCX via Wazzup contentUri + R2.

No real network. SEND/AUTO stay false unless a test arms them with mocks.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.messaging.document_send import send_booking_document
from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.outbound_guard import (
    DuplicateOutboundSuppressed,
    booking_document_crm_message_id,
    prepare_outbound_document,
    send_document_guarded,
)
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    set_manager_takeover,
)
from agent6_qualifier.messaging.r2_booking_upload import DOCX_CONTENT_TYPE
from agent6_qualifier.messaging.types import ConversationOwner, OutboundDocumentRequest
from agent6_qualifier.messaging.wazzup_client import WazzupClient
from agent6_qualifier.messaging.wazzup_config import load_wazzup_config
from agent6_qualifier.messaging.wazzup_errors import WazzupSendDisabled
from agent6_qualifier.messaging.wazzup_transport import WazzupWhatsAppTransport
from agent8_notary.service import process_confirmed_booking

EXPECTED_CHANNEL = "7ac4092a-cf3c-450f-8518-d7cc7bd3f995"
FAKE_KEY = "test-wazzup-key-not-real"
PUBLIC_DOC = (
    "https://cdn.example.test/bookings/OBJ1/wa_6681/"
    "Agreement_booking.docx"
)


@pytest.fixture
def wazzup_env(monkeypatch):
    monkeypatch.setenv("WAZZUP_API_KEY", FAKE_KEY)
    monkeypatch.setenv("WAZZUP_CHANNEL_ID", EXPECTED_CHANNEL)
    monkeypatch.setenv("WAZZUP_API_BASE_URL", "https://api.wazzup24.com")
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_WEBHOOK_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "false")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_REQUEST_TIMEOUT_SECONDS", "15")


def _live_env(monkeypatch):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "true")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_PHONES", "66812345678")


async def _run_in_thread(fn, *args):
    return fn(*args)


def _fake_transport_recorder():
    calls: list[tuple] = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body))
        if method == "POST" and url.endswith("/v3/message"):
            return 201, b'{"messageId":"m-doc-1"}'
        return 200, b"[]"

    return transport, calls


def test_wazzup_document_payload_contract(wazzup_env, monkeypatch):
    _live_env(monkeypatch)
    cfg = load_wazzup_config()
    http, calls = _fake_transport_recorder()
    client = WazzupClient(cfg, transport=http)
    client.send_document(
        chat_id="66812345678",
        content_uri=PUBLIC_DOC,
        crm_message_id="agent6-booking-doc-test-Agreement_booking.docx",
    )
    assert len(calls) == 1
    method, url, headers, body = calls[0]
    assert method == "POST"
    assert url.endswith("/v3/message")
    assert "Authorization" in headers
    payload = json.loads(body.decode())
    assert payload == {
        "channelId": EXPECTED_CHANNEL,
        "chatType": "whatsapp",
        "chatId": "66812345678",
        "contentUri": PUBLIC_DOC,
        "crmMessageId": "agent6-booking-doc-test-Agreement_booking.docx",
    }
    assert "text" not in payload


def test_send_document_guarded_metadata_and_dry_run(wazzup_env, tmp_path):
    cfg = load_wazzup_config()
    http, calls = _fake_transport_recorder()
    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="wa:66812345678")
    doc = tmp_path / "Agreement_booking.docx"
    doc.write_bytes(b"PK-fake-docx")
    req = prepare_outbound_document(
        recipient_chat_id="66812345678",
        content_uri=PUBLIC_DOC,
        filename=doc.name,
        content_type=DOCX_CONTENT_TYPE,
        crm_message_id=booking_document_crm_message_id(
            chat_id="wa:66812345678", filename=doc.name
        ),
        local_path=str(doc),
    )
    result = send_document_guarded(
        transport, req, ownership=ownership, dry_run=True
    )
    assert result.would_send is True
    assert result.content_uri == PUBLIC_DOC
    assert result.filename == doc.name
    assert calls == []


def test_guards_block_live_document_when_send_disabled(wazzup_env, monkeypatch):
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "true")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_PHONES", "66812345678")
    cfg = load_wazzup_config()
    http, calls = _fake_transport_recorder()
    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="wa:66812345678")
    req = prepare_outbound_document(
        recipient_chat_id="66812345678",
        content_uri=PUBLIC_DOC,
        filename="Agreement_booking.docx",
        crm_message_id="crm-doc-1",
    )
    with pytest.raises(WazzupSendDisabled):
        send_document_guarded(transport, req, ownership=ownership, dry_run=False)
    assert calls == []


def test_allowlist_blocks_document(wazzup_env, monkeypatch):
    _live_env(monkeypatch)
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_PHONES", "66999999999")
    cfg = load_wazzup_config()
    http, calls = _fake_transport_recorder()
    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="wa:66812345678")
    req = prepare_outbound_document(
        recipient_chat_id="66812345678",
        content_uri=PUBLIC_DOC,
        filename="Agreement_booking.docx",
        crm_message_id="crm-doc-allow",
    )
    with pytest.raises(WazzupSendDisabled):
        send_document_guarded(transport, req, ownership=ownership, dry_run=False)
    assert calls == []


def test_human_handoff_blocks_document(wazzup_env, monkeypatch):
    _live_env(monkeypatch)
    cfg = load_wazzup_config()
    http, calls = _fake_transport_recorder()
    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="wa:66812345678")
    set_manager_takeover(ownership, enabled=True)
    assert ownership.owner == ConversationOwner.HUMAN_HANDOFF
    req = prepare_outbound_document(
        recipient_chat_id="66812345678",
        content_uri=PUBLIC_DOC,
        filename="Agreement_booking.docx",
        crm_message_id="crm-doc-handoff",
    )
    with pytest.raises(PermissionError):
        send_document_guarded(transport, req, ownership=ownership, dry_run=False)
    assert calls == []


def test_duplicate_document_no_resend(wazzup_env, monkeypatch, tmp_path):
    _live_env(monkeypatch)
    cfg = load_wazzup_config()
    http, calls = _fake_transport_recorder()
    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="wa:66812345678")
    store = ProcessedEventStore(tmp_path / "events.sqlite")
    crm_id = booking_document_crm_message_id(
        chat_id="wa:66812345678", filename="Agreement_booking.docx"
    )
    req = prepare_outbound_document(
        recipient_chat_id="66812345678",
        content_uri=PUBLIC_DOC,
        filename="Agreement_booking.docx",
        crm_message_id=crm_id,
    )
    send_document_guarded(
        transport, req, ownership=ownership, store=store, dry_run=False
    )
    assert len(calls) == 1
    with pytest.raises(DuplicateOutboundSuppressed):
        send_document_guarded(
            transport, req, ownership=ownership, store=store, dry_run=False
        )
    assert len(calls) == 1


def test_r2_used_instead_of_drive_when_upload_required(wazzup_env, monkeypatch, tmp_path):
    _live_env(monkeypatch)
    cfg = load_wazzup_config()
    http, calls = _fake_transport_recorder()
    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="wa:66812345678")
    doc = tmp_path / "Agreement_booking.docx"
    doc.write_bytes(b"PK-fake")
    uploads: list[tuple] = []

    def fake_upload(path, *, object_id="", chat_id=""):
        uploads.append((str(path), object_id, chat_id))
        assert "drive.google" not in str(path).lower()
        return PUBLIC_DOC

    send_booking_document(
        transport,
        path=doc,
        recipient_chat_id="66812345678",
        ownership=ownership,
        object_id="OBJ1",
        chat_id="wa:66812345678",
        dry_run=False,
        upload_fn=fake_upload,
    )
    assert uploads == [(str(doc), "OBJ1", "wa:66812345678")]
    payload = json.loads(calls[0][3].decode())
    assert payload["contentUri"] == PUBLIC_DOC
    assert payload["contentUri"].startswith("https://")
    assert "text" not in payload


def test_agent8_wa_path_calls_send_document(wazzup_env, monkeypatch, tmp_path):
    """process_confirmed_booking WA send_doc uses shared sender (mocked)."""
    from agent6_qualifier.messaging import document_send as doc_mod

    doc = tmp_path / "Agreement_booking.docx"
    doc.write_bytes(b"PK")
    session = SimpleNamespace(
        chat_id="wa:66812345678",
        amo_lead_id=77,
        lead=SimpleNamespace(preferred_object_id="OBJ1"),
    )
    called: dict = {}

    def fake_send(*_a, **kwargs):
        called.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(doc_mod, "send_booking_document", fake_send)

    # Simulate WA send_doc wrapper used in wa_client_runtime
    ownership = ConversationOwnershipState(chat_id="wa:66812345678")
    cfg = load_wazzup_config()
    transport = WazzupWhatsAppTransport(cfg)

    async def send_doc(path):
        doc_mod.send_booking_document(
            transport,
            path=path,
            recipient_chat_id="66812345678",
            ownership=ownership,
            object_id="OBJ1",
            chat_id=session.chat_id,
            dry_run=True,
        )

    attach = MagicMock()
    result = asyncio.run(
        process_confirmed_booking(
            session,
            "WhatsApp: 66812345678",
            generate_doc=lambda s, c: doc,
            send_doc=send_doc,
            amo_lead_id=77,
            attach_file=attach,
            on_generation_error=lambda e: (_ for _ in ()).throw(e),
            on_attach_error=lambda e: (_ for _ in ()).throw(e),
            run_in_thread=_run_in_thread,
        )
    )
    assert result.sent_to_client is True
    assert result.attached_to_amo is True
    assert called["path"] == doc
    assert called["dry_run"] is True
    attach.assert_called_once_with(77, doc)


def test_send_failure_does_not_rollback_booking_or_amo(wazzup_env, tmp_path):
    doc = tmp_path / "Agreement_booking.docx"
    doc.write_bytes(b"PK")
    session = SimpleNamespace(chat_id="wa:1", amo_lead_id=99, lead=SimpleNamespace())
    attach = MagicMock()
    gen_errors: list[Exception] = []

    async def failing_send(_path):
        raise RuntimeError("WAZZUP_TRANSPORT_FAIL")

    result = asyncio.run(
        process_confirmed_booking(
            session,
            "WhatsApp: 1",
            generate_doc=lambda s, c: doc,
            send_doc=failing_send,
            amo_lead_id=99,
            attach_file=attach,
            on_generation_error=gen_errors.append,
            on_attach_error=lambda e: (_ for _ in ()).throw(e),
            run_in_thread=_run_in_thread,
        )
    )
    assert result.doc_path == doc
    assert result.sent_to_client is False
    assert result.attached_to_amo is True
    attach.assert_called_once_with(99, doc)
    assert str(gen_errors[0]) == "WAZZUP_TRANSPORT_FAIL"


def test_amo_attach_independent_of_wa_send(wazzup_env, tmp_path):
    doc = tmp_path / "Agreement_booking.docx"
    doc.write_bytes(b"PK")
    session = SimpleNamespace(chat_id="wa:1", amo_lead_id=55)
    attach = MagicMock()

    async def ok_send(_path):
        return None

    result = asyncio.run(
        process_confirmed_booking(
            session,
            "WhatsApp: 1",
            generate_doc=lambda s, c: doc,
            send_doc=ok_send,
            amo_lead_id=55,
            attach_file=attach,
            on_generation_error=lambda e: (_ for _ in ()).throw(e),
            on_attach_error=lambda e: (_ for _ in ()).throw(e),
            run_in_thread=_run_in_thread,
        )
    )
    assert result.sent_to_client is True
    assert result.attached_to_amo is True
    attach.assert_called_once()


def test_telegram_send_file_unchanged_path(tmp_path):
    """TG Agent8 still uses client.send_file — not Wazzup document API."""
    doc = tmp_path / "Agreement_booking.docx"
    doc.write_bytes(b"PK")
    client = SimpleNamespace(send_file=AsyncMock())
    session = SimpleNamespace(chat_id="123", amo_lead_id=None)

    async def send_doc(path):
        await client.send_file(
            123,
            str(path),
            caption="booking",
            reply_to=1,
        )

    result = asyncio.run(
        process_confirmed_booking(
            session,
            "Telegram: @u",
            generate_doc=lambda s, c: doc,
            send_doc=send_doc,
            amo_lead_id=None,
            attach_file=None,
            on_generation_error=lambda e: (_ for _ in ()).throw(e),
            on_attach_error=lambda e: (_ for _ in ()).throw(e),
            run_in_thread=_run_in_thread,
        )
    )
    assert result.sent_to_client is True
    client.send_file.assert_awaited_once_with(
        123, str(doc), caption="booking", reply_to=1
    )


def test_outbound_document_request_content_type():
    req = OutboundDocumentRequest(
        recipient_chat_id="1",
        content_uri=PUBLIC_DOC,
        filename="Agreement_booking.docx",
    )
    assert req.content_type == DOCX_CONTENT_TYPE


def test_dry_run_skips_r2_and_network(wazzup_env, tmp_path):
    cfg = load_wazzup_config()
    http, calls = _fake_transport_recorder()
    transport = WazzupWhatsAppTransport(cfg, client=WazzupClient(cfg, transport=http))
    ownership = ConversationOwnershipState(chat_id="wa:66812345678")
    doc = tmp_path / "Agreement_booking.docx"
    doc.write_bytes(b"PK")

    def boom_upload(*_a, **_k):
        raise AssertionError("R2 must not run on dry_run")

    result = send_booking_document(
        transport,
        path=doc,
        recipient_chat_id="66812345678",
        ownership=ownership,
        dry_run=True,
        upload_fn=boom_upload,
    )
    assert result.would_send is True
    assert "dry-run" in (result.content_uri or "")
    assert calls == []
