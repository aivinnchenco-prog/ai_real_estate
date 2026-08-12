"""Offline tests for controlled WA Agent7 owner correlation / safety gates."""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "contact_role" / "src"))

from agent6_qualifier.messaging.ownership import ConversationOwnershipState
from agent6_qualifier.messaging.ownership_store import OwnershipStore
from agent6_qualifier.messaging.types import (
    CanonicalInboundMessage,
    ConversationOwner,
)
from agent6_qualifier.messaging.wa_owner_runtime import process_whatsapp_owner_turn
from agent6_qualifier.models import Availability, LeadProfile, Listing
from agent6_qualifier.qualifier import Session
from agent6_qualifier.sessions import SessionStore
from agent7_envoy.auto import (
    _client_phone_digits,
    _manual_owner_role_conflict,
    _normalize_owner_phone,
)
from agent7_envoy.owner_request_store import OwnerRequestStore
from agent7_envoy.owner_result import OwnerVerdict
from agent7_envoy.wa_owner_resolve import resolve_whatsapp_owner_session
from contact_role.roles import CanonicalRole
from contact_role.sources import RoleSource
from contact_role.state import ContactRoleStore


def _listing(**kwargs) -> Listing:
    base = dict(
        object_id="A_20260810_001",
        page_id="page-1",
        title="Villa",
        owner_whatsapp="+66999990001",
        owner_agent_type="Владелец",
        availability=Availability.UNKNOWN,
    )
    base.update(kwargs)
    return Listing(**base)


def _session(chat_id: str, listing: Listing, *, phone: str) -> Session:
    return Session(
        chat_id=chat_id,
        lead=LeadProfile(
            name="Client",
            whatsapp=phone,
            check_in=date(2026, 9, 1),
            check_out=date(2026, 9, 30),
            preferred_object_id=listing.object_id,
        ),
        chosen=listing,
        awaiting_owner=True,
    )


def _msg(*, phone: str, text: str = "свободно", message_id: str = "om1") -> CanonicalInboundMessage:
    return CanonicalInboundMessage(
        provider="wazzup",
        channel="whatsapp",
        message_id=message_id,
        chat_id=phone.lstrip("+"),
        phone=phone.lstrip("+"),
        direction="inbound",
        text=text,
        timestamp=None,
    )


@pytest.fixture
def stores(tmp_path, monkeypatch):
    sess = SessionStore(tmp_path / "sessions")
    req = OwnerRequestStore(tmp_path / "owner_requests.json")
    own = OwnershipStore(tmp_path / "wa_ownership.json")
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_ENABLED", "true")
    monkeypatch.setenv("WAZZUP_LIVE_ALLOWLIST_PHONES", "+66625124001,+66999990001")
    monkeypatch.setenv("AGENT7_LIVE_OUTREACH_ENABLED", "false")
    return sess, req, own


def test_01_registry_request_resolves_correct_session(stores):
    sess, req, _own = stores
    listing = _listing()
    s = _session("wa_66625124001", listing, phone="+66625124001")
    sess.save(s)
    created = req.create(
        owner_phone="+66999990001",
        object_id=listing.object_id,
        client_session_chat_id=s.chat_id,
    )
    req.mark_awaiting(created.owner_request_id)
    resolved = resolve_whatsapp_owner_session(
        owner_phone="+66999990001",
        session_store=sess,
        request_store=req,
        registry_entry={
            "object_id": listing.object_id,
            "owner_request_id": created.owner_request_id,
        },
    )
    assert resolved.code == "OK"
    assert resolved.session is not None
    assert resolved.session.chat_id == s.chat_id


def test_02_registry_absent_one_awaiting_wa_fallback(stores):
    sess, req, _own = stores
    listing = _listing()
    s = _session("wa_66625124001", listing, phone="+66625124001")
    sess.save(s)
    resolved = resolve_whatsapp_owner_session(
        owner_phone="+66999990001",
        session_store=sess,
        request_store=req,
        registry_entry=None,
    )
    assert resolved.code == "OK"
    assert resolved.session.chat_id == s.chat_id


def test_03_registry_absent_no_awaiting_not_found(stores):
    sess, req, _own = stores
    resolved = resolve_whatsapp_owner_session(
        owner_phone="+66999990001",
        session_store=sess,
        request_store=req,
        registry_entry=None,
    )
    assert resolved.code == "OWNER_REQUEST_NOT_FOUND"


def test_04_two_awaiting_same_object_ambiguous(stores):
    sess, req, _own = stores
    listing = _listing()
    s1 = _session("wa_66625124001", listing, phone="+66625124001")
    s2 = _session("wa_66625124002", listing, phone="+66625124002")
    # Same owner WA on both chosen listings.
    sess.save(s1)
    sess.save(s2)
    resolved = resolve_whatsapp_owner_session(
        owner_phone="+66999990001",
        session_store=sess,
        request_store=req,
        registry_entry={"object_id": listing.object_id},
    )
    assert resolved.code == "OWNER_REQUEST_AMBIGUOUS"


def test_05_request_specific_selects_correct_client(stores):
    sess, req, _own = stores
    listing = _listing()
    s1 = _session("wa_66625124001", listing, phone="+66625124001")
    s2 = _session("wa_66625124002", listing, phone="+66625124002")
    sess.save(s1)
    sess.save(s2)
    r2 = req.create(
        owner_phone="+66999990001",
        object_id=listing.object_id,
        client_session_chat_id=s2.chat_id,
    )
    req.mark_awaiting(r2.owner_request_id)
    resolved = resolve_whatsapp_owner_session(
        owner_phone="+66999990001",
        session_store=sess,
        request_store=req,
        registry_entry={"object_id": listing.object_id},
    )
    assert resolved.code == "OK"
    assert resolved.session.chat_id == s2.chat_id


def test_06_duplicate_owner_webhook_no_second_continuation(stores, monkeypatch):
    sess, req, own = stores
    listing = _listing()
    s = _session("wa_66625124001", listing, phone="+66625124001")
    sess.save(s)
    created = req.create(
        owner_phone="+66999990001",
        object_id=listing.object_id,
        client_session_chat_id=s.chat_id,
    )
    req.mark_awaiting(created.owner_request_id)
    own.save(
        ConversationOwnershipState(
            chat_id=s.chat_id, owner=ConversationOwner.BOT_ACTIVE
        ),
        phone="+66625124001",
    )

    monkeypatch.setattr(
        "agent7_envoy.owner_result.parse_owner_reply",
        lambda text, session: OwnerVerdict(status="free"),
    )
    monkeypatch.setattr(
        "agent6_qualifier.brain.polish_reply",
        lambda draft, language, name: draft,
    )
    monkeypatch.setattr(
        "agent6_qualifier.notion_store.update_availability",
        lambda *a, **k: None,
    )

    msg = _msg(phone="+66999990001", message_id="dup-1")
    r1 = asyncio.run(
        process_whatsapp_owner_turn(
            msg, store=sess, request_store=req, ownership_store=own
        )
    )
    r2 = asyncio.run(
        process_whatsapp_owner_turn(
            msg, store=sess, request_store=req, ownership_store=own
        )
    )
    assert r1.processed is True
    assert r2.resolve_code == "OWNER_REPLY_DUPLICATE"
    assert r2.client_notified is False


@pytest.mark.parametrize(
    "verdict_status",
    ["free", "busy", "conditions_changed"],
)
def test_14_15_16_verdicts_route_correct_client(stores, monkeypatch, verdict_status):
    sess, req, own = stores
    listing = _listing()
    s = _session("wa_66625124001", listing, phone="+66625124001")
    sess.save(s)
    created = req.create(
        owner_phone="+66999990001",
        object_id=listing.object_id,
        client_session_chat_id=s.chat_id,
    )
    req.mark_awaiting(created.owner_request_id)
    own.save(
        ConversationOwnershipState(
            chat_id=s.chat_id, owner=ConversationOwner.BOT_ACTIVE
        ),
        phone="+66625124001",
    )
    verdict = OwnerVerdict(
        status=verdict_status,
        busy_until=date(2026, 9, 10) if verdict_status == "busy" else None,
        conditions_note="цена другая" if verdict_status == "conditions_changed" else "",
    )
    monkeypatch.setattr(
        "agent7_envoy.owner_result.parse_owner_reply",
        lambda text, session: verdict,
    )
    monkeypatch.setattr(
        "agent6_qualifier.brain.polish_reply",
        lambda draft, language, name: draft,
    )
    monkeypatch.setattr(
        "agent6_qualifier.notion_store.update_availability",
        lambda *a, **k: None,
    )
    turn = asyncio.run(
        process_whatsapp_owner_turn(
            _msg(phone="+66999990001", message_id=f"m-{verdict_status}"),
            store=sess,
            request_store=req,
            ownership_store=own,
        )
    )
    assert turn.processed is True
    assert turn.client_session_chat_id == s.chat_id
    assert turn.client_notified is True


def test_07_bot_active_notify_allowed(stores, monkeypatch):
    test_14_15_16_verdicts_route_correct_client(stores, monkeypatch, "free")


def test_08_human_handoff_blocks_client_notify(stores, monkeypatch):
    sess, req, own = stores
    listing = _listing()
    s = _session("wa_66625124001", listing, phone="+66625124001")
    sess.save(s)
    created = req.create(
        owner_phone="+66999990001",
        object_id=listing.object_id,
        client_session_chat_id=s.chat_id,
    )
    req.mark_awaiting(created.owner_request_id)
    own.save(
        ConversationOwnershipState(
            chat_id=s.chat_id, owner=ConversationOwner.HUMAN_HANDOFF
        ),
        phone="+66625124001",
    )
    monkeypatch.setattr(
        "agent7_envoy.owner_result.parse_owner_reply",
        lambda text, session: OwnerVerdict(status="free"),
    )
    monkeypatch.setattr(
        "agent6_qualifier.brain.polish_reply",
        lambda draft, language, name: draft,
    )
    monkeypatch.setattr(
        "agent6_qualifier.notion_store.update_availability",
        lambda *a, **k: None,
    )
    turn = asyncio.run(
        process_whatsapp_owner_turn(
            _msg(phone="+66999990001", message_id="hh1"),
            store=sess,
            request_store=req,
            ownership_store=own,
        )
    )
    assert turn.processed is True
    assert turn.client_notified is False
    assert any("HUMAN_HANDOFF" in n or "blocked" in n for n in turn.notes)


def test_09_ownership_none_no_crash(stores, monkeypatch):
    sess, req, own = stores
    listing = _listing()
    s = _session("wa_66625124001", listing, phone="+66625124001")
    sess.save(s)
    created = req.create(
        owner_phone="+66999990001",
        object_id=listing.object_id,
        client_session_chat_id=s.chat_id,
    )
    req.mark_awaiting(created.owner_request_id)
    # no ownership saved
    monkeypatch.setattr(
        "agent7_envoy.owner_result.parse_owner_reply",
        lambda text, session: OwnerVerdict(status="free"),
    )
    monkeypatch.setattr(
        "agent6_qualifier.brain.polish_reply",
        lambda draft, language, name: draft,
    )
    monkeypatch.setattr(
        "agent6_qualifier.notion_store.update_availability",
        lambda *a, **k: None,
    )
    turn = asyncio.run(
        process_whatsapp_owner_turn(
            _msg(phone="+66999990001", message_id="none1"),
            store=sess,
            request_store=req,
            ownership_store=own,
        )
    )
    assert turn.processed is True
    assert turn.client_notified is False
    assert any("ownership unknown" in n for n in turn.notes)


def test_13_mirror_failure_non_blocking(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTACT_ROLE_STORE_PATH", str(tmp_path / "missing-dir" / "x.json"))
    # Corrupt/missing store must not raise — outreach gate stays non-blocking.
    assert _manual_owner_role_conflict("66999990001") is None


def test_10_identity_conflict_detection():
    s = SimpleNamespace(
        chat_id="wa_66625124001",
        lead=SimpleNamespace(whatsapp="+66625124001"),
    )
    assert _client_phone_digits(s) == _normalize_owner_phone("+66625124001")


def test_11_manual_client_blocks_outreach(tmp_path, monkeypatch):
    path = tmp_path / "roles.json"
    monkeypatch.setenv("CONTACT_ROLE_STORE_PATH", str(path))
    store = ContactRoleStore(path)
    from contact_role.assign import assign_known_role

    assign_known_role(
        phone="+66999990001",
        role=CanonicalRole.CLIENT,
        source=RoleSource.MANUAL,
        store=store,
        enqueue_sync=False,
    )
    assert _manual_owner_role_conflict("66999990001") == "OWNER_ROLE_MANUAL_CONFLICT"


def test_12_manual_owner_allowed(tmp_path, monkeypatch):
    path = tmp_path / "roles.json"
    monkeypatch.setenv("CONTACT_ROLE_STORE_PATH", str(path))
    store = ContactRoleStore(path)
    from contact_role.assign import assign_known_role

    assign_known_role(
        phone="+66999990001",
        role=CanonicalRole.OWNER,
        source=RoleSource.MANUAL,
        store=store,
        enqueue_sync=False,
    )
    assert _manual_owner_role_conflict("66999990001") is None


def test_17_telegram_owner_handler_unchanged_signature():
    from agent7_envoy.owner_handler import process_owner_message
    import inspect

    sig = inspect.signature(process_owner_message)
    assert "preresolved_session" in sig.parameters
    assert sig.parameters["preresolved_session"].default is None


def test_18_no_live_network_flags(stores):
    import os

    assert os.getenv("WAZZUP_SEND_ENABLED", "false").lower() in {"false", "0", ""}
    assert os.getenv("AGENT7_LIVE_OUTREACH_ENABLED", "false").lower() in {
        "false",
        "0",
        "",
    }
