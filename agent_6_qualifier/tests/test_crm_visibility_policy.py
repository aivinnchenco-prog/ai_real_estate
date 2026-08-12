"""CRM channel visibility policy for Agent7 owner outreach."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.models import LeadProfile, Listing, OwnerChannel
from agent6_qualifier.qualifier import Session
from agent7_envoy.crm_business_sync import (
    attempt_raw_chat_mirror,
    owner_reply_business_note,
    outreach_started_note,
    persist_owner_result,
    sync_owner_outreach_started,
    sync_owner_reply_business,
)
from agent7_envoy.crm_visibility import (
    CrmVisibilityMode,
    resolve_owner_channel_crm_policy,
    should_mirror_raw_chat,
)
from agent7_envoy.messaging.inbox_watcher import InboundOwnerMessage
from agent7_envoy.owner_request_store import OwnerRequestStore
from agent7_envoy.owner_result import OwnerVerdict
from agent7_envoy.source_native_reply import (
    fake_owner_contact_guard,
    handle_source_native_owner_reply,
)


def _session(chat_id: str, object_id: str, *, amo_lead_id: int | None = 99) -> Session:
    lead = LeadProfile(
        check_in=date(2026, 8, 1),
        check_out=date(2026, 9, 1),
        guests=2,
        preferred_object_id=object_id,
        name="Client",
    )
    chosen = Listing(object_id=object_id, title="Villa")
    s = Session(chat_id=chat_id, lead=lead, chosen=chosen, awaiting_owner=True)
    s.amo_lead_id = amo_lead_id
    return s


# ---- 1–4 policy matrix ----

def test_1_wa_crm_visibility_full_chat():
    p = resolve_owner_channel_crm_policy("whatsapp")
    assert p.raw_chat_mirroring is True
    assert p.business_events is True
    assert p.visibility.value in {"FULL_CHAT", "FULL_CHAT_EXTERNAL_SYNC"}


def test_2_tg_crm_visibility_actual_no_raw_mirror():
    """Code has no TG→amo chat connector — BUSINESS_EVENTS_ONLY."""
    p = resolve_owner_channel_crm_policy("telegram")
    assert p.raw_chat_mirroring is False
    assert p.business_events is True
    assert p.visibility == CrmVisibilityMode.BUSINESS_EVENTS_ONLY


def test_3_fb_business_events_only():
    p = resolve_owner_channel_crm_policy(OwnerChannel.FACEBOOK_MESSENGER)
    # Without amo custom channel config → business events only
    assert p.visibility == CrmVisibilityMode.BUSINESS_EVENTS_ONLY
    assert p.raw_chat_mirroring is False
    assert p.business_events is True


def test_4_airbnb_business_events_only():
    p = resolve_owner_channel_crm_policy(OwnerChannel.AIRBNB_MESSAGES)
    assert p.visibility == CrmVisibilityMode.BUSINESS_EVENTS_ONLY
    assert p.raw_chat_mirroring is False
    assert p.business_events is True


# ---- 5–8 raw mirror never called / expected skip ----

def test_5_fb_raw_mirror_never_called():
    calls = []

    class FakeAmo:
        def note_owner(self, *a, **k):
            calls.append(("note", a))

        def send_chat_message(self, *a, **k):  # must never be used
            calls.append(("raw_chat", a))
            raise AssertionError("raw chat must not be called")

    r = attempt_raw_chat_mirror(channel="facebook_messenger", amo=FakeAmo())
    assert r.raw_chat_skipped_expected is True
    assert r.error_code == "CRM_RAW_CHAT_UNSUPPORTED_FOR_CHANNEL"
    assert not any(c[0] == "raw_chat" for c in calls)


def test_6_airbnb_raw_mirror_never_called():
    r = attempt_raw_chat_mirror(channel="airbnb_messages")
    assert r.raw_chat_skipped_expected is True
    assert should_mirror_raw_chat("airbnb_messages") is False


def test_7_fb_expected_mirror_skip_not_failure():
    r = attempt_raw_chat_mirror(channel="facebook_messenger")
    assert r.raw_chat_skipped_expected is True
    assert r.business_failed is False  # skip ≠ failure


def test_8_airbnb_expected_mirror_skip_not_failure():
    r = attempt_raw_chat_mirror(channel="airbnb_messages")
    assert r.business_failed is False
    assert "SKIPPED_EXPECTED" in r.raw_chat_action or r.raw_chat_skipped_expected


# ---- 9–10 outreach business events ----

def test_9_fb_outreach_business_event_written():
    amo = MagicMock()
    amo.ensure_pipeline.return_value = {"Согласование условий": 1}
    session = _session("wa_1", "F_1")
    r = sync_owner_outreach_started(
        amo, session, channel="facebook_messenger", object_id="F_1",
        owner_request_id="orq-fb-1", dry_run=False,
    )
    assert r.business_synced is True
    assert r.raw_chat_skipped_expected is True
    note = amo.note_owner.call_args[0][2]
    assert "Facebook Messenger" in note
    assert "Можно посмотреть" not in note  # no raw script dump required


def test_10_airbnb_outreach_business_event_written():
    amo = MagicMock()
    session = _session("tg_1", "A_1")
    r = sync_owner_outreach_started(
        amo, session, channel="airbnb_messages", object_id="A_1", dry_run=False,
    )
    assert r.business_synced is True
    note = amo.note_owner.call_args[0][2]
    assert "Airbnb" in note


# ---- 11–18 persist + downstream ----

def _prep_req(store: OwnerRequestStore, *, channel: str, oid: str, client: str):
    req = store.create(
        object_id=oid,
        client_session_chat_id=client,
        channel=channel,
        source=channel.upper(),
        source_url=f"https://example.com/{oid}",
    )
    store.mark_sent(req.owner_request_id, external_thread_id=f"t-{oid}")
    store.mark_awaiting(req.owner_request_id)
    return store.get(req.owner_request_id)


@pytest.mark.parametrize(
    "channel,oid,client,text,expected",
    [
        ("facebook_messenger", "F_free", "wa_c1", "Да, свободно", "free"),
        ("facebook_messenger", "F_busy", "wa_c2", "Занято до ноября", "busy"),
        ("facebook_messenger", "F_cond", "wa_c3", "Цена 180000", "conditions_changed"),
        ("airbnb_messages", "A_free", "tg_c1", "yes available", "free"),
        ("airbnb_messages", "A_busy", "tg_c2", "busy / занято", "busy"),
        ("airbnb_messages", "A_cond", "tg_c3", "новая цена условия", "conditions_changed"),
    ],
)
def test_11_18_source_native_verdicts_persist_and_continue(
    tmp_path, channel, oid, client, text, expected
):
    store = OwnerRequestStore(path=tmp_path / f"{oid}.json")
    req = _prep_req(store, channel=channel, oid=oid, client=client)
    session = _session(client, oid)
    session.owner_request_id = req.owner_request_id
    notified = []

    inbound = InboundOwnerMessage(
        channel=channel,
        text=text,
        message_id=f"m-{oid}",
        external_thread_id=req.external_thread_id,
        owner_request_id=req.owner_request_id,
        object_id=oid,
        client_session_chat_id=client,
    )
    result = handle_source_native_owner_reply(
        inbound,
        req,
        session=session,
        request_store=store,
        amo=None,
        notify_client=lambda cid, msg: notified.append((cid, msg)),
        dry_run_crm=True,
    )
    assert result.processed is True
    assert result.verdict_status == expected
    assert result.owner_result_persisted is True
    assert result.downstream_continued is True
    assert result.raw_chat_mirror == "SKIPPED_EXPECTED"
    assert notified and notified[0][0] == client
    saved = store.get(req.owner_request_id)
    assert saved is not None
    assert saved.owner_result.get("availability") == expected
    assert saved.owner_result.get("channel") == channel


# ---- 19–20 no amo message id required ----

def test_19_fb_correlation_without_amo_message_id(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "fb.json")
    req = _prep_req(
        store, channel="facebook_messenger", oid="F_x", client="wa_x"
    )
    # No amo_crm_message_id field used
    assert not hasattr(req, "amo_message_id") or not getattr(req, "amo_message_id", "")
    session = _session("wa_x", "F_x", amo_lead_id=None)
    inbound = InboundOwnerMessage(
        channel="facebook_messenger",
        text="свободно",
        message_id="ext-only-1",
        external_thread_id=req.external_thread_id,
        owner_request_id=req.owner_request_id,
        object_id="F_x",
        client_session_chat_id="wa_x",
    )
    r = handle_source_native_owner_reply(
        inbound, req, session=session, request_store=store, amo=None
    )
    assert r.processed is True
    assert r.client_session_chat_id == "wa_x"


def test_20_airbnb_correlation_without_amo_message_id(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "ab.json")
    req = _prep_req(store, channel="airbnb_messages", oid="A_x", client="tg_x")
    session = _session("tg_x", "A_x", amo_lead_id=None)
    inbound = InboundOwnerMessage(
        channel="airbnb_messages",
        text="ok",
        message_id="ext-ab-1",
        external_thread_id=req.external_thread_id,
        owner_request_id=req.owner_request_id,
        object_id="A_x",
        client_session_chat_id="tg_x",
    )
    r = handle_source_native_owner_reply(
        inbound, req, session=session, request_store=store, amo=None
    )
    assert r.processed is True


# ---- 21 amo failure isolation ----

def test_21_amo_failure_does_not_destroy_owner_result(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "fail.json")
    req = _prep_req(
        store, channel="facebook_messenger", oid="F_fail", client="wa_fail"
    )
    session = _session("wa_fail", "F_fail")

    class BoomAmo:
        def ensure_pipeline(self):
            raise RuntimeError("amo down")

        def note_owner(self, *a, **k):
            raise RuntimeError("amo down")

    inbound = InboundOwnerMessage(
        channel="facebook_messenger",
        text="свободно",
        message_id="m-fail",
        external_thread_id=req.external_thread_id,
        owner_request_id=req.owner_request_id,
        object_id="F_fail",
        client_session_chat_id="wa_fail",
    )
    r = handle_source_native_owner_reply(
        inbound,
        req,
        session=session,
        request_store=store,
        amo=BoomAmo(),
        dry_run_crm=False,
    )
    assert r.owner_result_persisted is True
    assert r.business_event_sync == "FAILED"
    saved = store.get(req.owner_request_id)
    assert saved is not None
    assert saved.owner_result.get("availability") == "free"


# ---- 22–23 no fake attribution ----

def test_22_23_no_fake_wa_tg_attribution():
    note = outreach_started_note("facebook_messenger")
    assert "WhatsApp" not in note
    assert "Telegram" not in note
    reply_note = owner_reply_business_note(
        channel="airbnb_messages",
        verdict_status="free",
        include_raw_excerpt="should not appear for source-native",
    )
    assert "should not appear" not in reply_note
    listing = SimpleNamespace(owner_whatsapp="", owner_telegram="")
    assert fake_owner_contact_guard(listing) == []
    bad = SimpleNamespace(owner_whatsapp="fake:+66000", owner_telegram="")
    assert fake_owner_contact_guard(bad)


def test_persist_owner_result_sets_structured_fields(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "res.json")
    req = store.create(
        object_id="X", client_session_chat_id="c", channel="facebook_messenger"
    )
    v = OwnerVerdict(
        status="conditions_changed",
        new_price_month=180000,
        conditions_note="deposit",
    )
    persist_owner_result(store, req.owner_request_id, verdict=v, channel="facebook_messenger")
    saved = store.get(req.owner_request_id)
    assert saved.owner_result["new_price"] == 180000
    assert saved.owner_result["conditions_note"] == "deposit"
    assert saved.crm_visibility == "BUSINESS_EVENTS_ONLY"


def test_live_flags_remain_off(monkeypatch):
    monkeypatch.delenv("AGENT7_LIVE_OUTREACH_ENABLED", raising=False)
    monkeypatch.delenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", raising=False)
    monkeypatch.delenv("AGENT7_AIRBNB_MESSAGES_ENABLED", raising=False)
    from agent7_envoy.messaging.base import (
        agent7_live_enabled,
        airbnb_messages_enabled,
        facebook_messenger_enabled,
    )

    assert agent7_live_enabled() is False
    assert facebook_messenger_enabled() is False
    assert airbnb_messages_enabled() is False
