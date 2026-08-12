"""Offline/mocked tests for Facebook + Airbnb owner transports."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.models import LeadProfile, Listing, OwnerChannel
from agent7_envoy.messaging.airbnb_messages import AirbnbMessagesOwnerTransport
from agent7_envoy.messaging.base import AuthStatus, SendOutcome, SendTextRequest
from agent7_envoy.messaging.dispatch import dispatch_outreach_plan
from agent7_envoy.messaging.facebook_messenger import FacebookMessengerOwnerTransport
from agent7_envoy.messaging.rate_guard import AirbnbOwnerMessageRateGuard
from agent7_envoy.owner_request_store import OwnerRequestStore
from agent7_envoy.outreach import OutreachPlan


def _fb_ok_nav(_req):
    return {
        "listing_opened": True,
        "message_action_found": True,
        "composer_found": True,
        "thread_identified": True,
        "seller_context_confirmed": True,
        "thread_id": "fb-thread-1",
        "conversation_url": "https://www.facebook.com/messages/t/fb-thread-1",
    }


def _airbnb_ok_nav(_req):
    return {
        "listing_opened": True,
        "message_action_found": True,
        "composer_found": True,
        "thread_identified": True,
        "thread_id": "ab-thread-1",
        "conversation_url": "https://www.airbnb.com/guest/inbox/ab-thread-1",
    }


def test_facebook_auth_required(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", "true")
    t = FacebookMessengerOwnerTransport(
        profile_dir=tmp_path / "fb_profile",
        auth_probe=lambda: AuthStatus.AUTH_REQUIRED,
    )
    r = t.send_text(
        SendTextRequest(
            text="hello",
            destination="https://www.facebook.com/marketplace/item/1",
            source_url="https://www.facebook.com/marketplace/item/1",
            dry_run=True,
        )
    )
    assert r.outcome == SendOutcome.AUTH_REQUIRED


def test_facebook_listing_opens_and_composer(monkeypatch):
    monkeypatch.setenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", "true")
    t = FacebookMessengerOwnerTransport(
        auth_probe=lambda: AuthStatus.READY,
        navigator=_fb_ok_nav,
    )
    r = t.send_text(
        SendTextRequest(
            text="Agent7 message",
            destination="https://www.facebook.com/marketplace/item/1",
            source_url="https://www.facebook.com/marketplace/item/1",
            dry_run=True,
        )
    )
    assert r.outcome == SendOutcome.DRY_RUN_READY
    assert r.diagnostics.listing_opened
    assert r.diagnostics.composer_found
    assert r.diagnostics.seller_context_confirmed
    assert r.external_thread_id == "fb-thread-1"


def test_facebook_dry_run_no_send(monkeypatch):
    monkeypatch.setenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", "true")
    monkeypatch.setenv("AGENT7_LIVE_OUTREACH_ENABLED", "true")
    t = FacebookMessengerOwnerTransport(
        auth_probe=lambda: AuthStatus.READY,
        navigator=_fb_ok_nav,
    )
    r = t.send_text(
        SendTextRequest(
            text="x",
            destination="https://www.facebook.com/marketplace/item/1",
            source_url="https://www.facebook.com/marketplace/item/1",
            dry_run=True,
        )
    )
    assert r.outcome == SendOutcome.DRY_RUN_READY
    assert r.dry_run is True


def test_facebook_seller_context_required(monkeypatch):
    monkeypatch.setenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", "true")

    def nav(_req):
        d = _fb_ok_nav(_req)
        d["seller_context_confirmed"] = False
        return d

    t = FacebookMessengerOwnerTransport(
        auth_probe=lambda: AuthStatus.READY, navigator=nav
    )
    r = t.send_text(
        SendTextRequest(
            text="x",
            destination="https://www.facebook.com/marketplace/item/1",
            source_url="https://www.facebook.com/marketplace/item/1",
            dry_run=True,
        )
    )
    assert r.outcome == SendOutcome.CONTEXT_MISMATCH


def test_facebook_thread_mismatch_blocked(monkeypatch):
    monkeypatch.setenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", "true")
    t = FacebookMessengerOwnerTransport(
        auth_probe=lambda: AuthStatus.READY, navigator=_fb_ok_nav
    )
    r = t.send_text(
        SendTextRequest(
            text="x",
            destination="https://www.facebook.com/marketplace/item/1",
            source_url="https://www.facebook.com/marketplace/item/1",
            dry_run=True,
            expected_thread_id="other-thread",
        )
    )
    assert r.outcome == SendOutcome.CONTEXT_MISMATCH
    assert r.blocker == "external_thread_mismatch"


def test_facebook_duplicate_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", "true")
    store = OwnerRequestStore(path=tmp_path / "reqs.json")
    listing = Listing(
        object_id="F_1",
        source_url="https://www.facebook.com/marketplace/item/1",
    )
    plan = OutreachPlan(
        listing=listing,
        channel=OwnerChannel.FACEBOOK_MESSENGER,
        contact=listing.source_url,
        first_message="hello owner",
    )
    fb = FacebookMessengerOwnerTransport(
        auth_probe=lambda: AuthStatus.READY, navigator=_fb_ok_nav
    )
    # First create + mark awaiting to simulate prior send
    req = store.create(
        object_id="F_1",
        client_session_chat_id="wa_1",
        channel="facebook_messenger",
        source="FACEBOOK",
        source_url=listing.source_url,
        outbound_message="hello owner",
    )
    store.mark_sent(req.owner_request_id, external_thread_id="fb-thread-1")
    store.mark_awaiting(req.owner_request_id)

    d = dispatch_outreach_plan(
        plan,
        client_session_chat_id="wa_1",
        request_store=store,
        dry_run=True,
        facebook_transport=fb,
    )
    assert d.duplicate_blocked is True
    assert d.result.outcome == SendOutcome.DUPLICATE


def test_airbnb_auth_required(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT7_AIRBNB_MESSAGES_ENABLED", "true")
    t = AirbnbMessagesOwnerTransport(
        profile_dir=tmp_path / "ab_profile",
        auth_probe=lambda: AuthStatus.AUTH_REQUIRED,
        rate_guard=AirbnbOwnerMessageRateGuard(path=tmp_path / "rg.json"),
    )
    r = t.send_text(
        SendTextRequest(
            text="hi",
            destination="https://www.airbnb.com/rooms/1",
            source_url="https://www.airbnb.com/rooms/1",
            dry_run=True,
        )
    )
    assert r.outcome == SendOutcome.AUTH_REQUIRED


def test_airbnb_contact_host_and_composer(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT7_AIRBNB_MESSAGES_ENABLED", "true")
    t = AirbnbMessagesOwnerTransport(
        auth_probe=lambda: AuthStatus.READY,
        navigator=_airbnb_ok_nav,
        rate_guard=AirbnbOwnerMessageRateGuard(path=tmp_path / "rg.json"),
    )
    r = t.send_text(
        SendTextRequest(
            text="Можно посмотреть дом",
            destination="https://www.airbnb.com/rooms/1",
            source_url="https://www.airbnb.com/rooms/1",
            dry_run=True,
        )
    )
    assert r.outcome == SendOutcome.DRY_RUN_READY
    assert r.diagnostics.message_action_found
    assert r.diagnostics.composer_found


def test_airbnb_rate_limit_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT7_AIRBNB_MESSAGES_ENABLED", "true")
    rg = AirbnbOwnerMessageRateGuard(
        path=tmp_path / "rg.json", max_per_hour=1, max_per_day=1
    )
    rg.record_send()
    t = AirbnbMessagesOwnerTransport(
        auth_probe=lambda: AuthStatus.READY,
        navigator=_airbnb_ok_nav,
        rate_guard=rg,
    )
    r = t.send_text(
        SendTextRequest(
            text="hi",
            destination="https://www.airbnb.com/rooms/1",
            source_url="https://www.airbnb.com/rooms/1",
            dry_run=True,
        )
    )
    assert r.outcome == SendOutcome.RATE_LIMITED
    assert r.blocker == "AIRBNB_RATE_LIMIT_GUARD"


def test_airbnb_unrelated_thread_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT7_AIRBNB_MESSAGES_ENABLED", "true")

    def nav(_req):
        d = _airbnb_ok_nav(_req)
        d["unrelated_thread"] = True
        return d

    t = AirbnbMessagesOwnerTransport(
        auth_probe=lambda: AuthStatus.READY,
        navigator=nav,
        rate_guard=AirbnbOwnerMessageRateGuard(path=tmp_path / "rg.json"),
    )
    r = t.send_text(
        SendTextRequest(
            text="hi",
            destination="https://www.airbnb.com/rooms/1",
            source_url="https://www.airbnb.com/rooms/1",
            dry_run=True,
        )
    )
    assert r.outcome == SendOutcome.CONTEXT_MISMATCH
    assert r.blocker == "unrelated_thread"


def test_airbnb_duplicate_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT7_AIRBNB_MESSAGES_ENABLED", "true")
    store = OwnerRequestStore(path=tmp_path / "reqs.json")
    listing = Listing(object_id="A_1", source_url="https://www.airbnb.com/rooms/1")
    plan = OutreachPlan(
        listing=listing,
        channel=OwnerChannel.AIRBNB_MESSAGES,
        contact=listing.source_url,
        first_message="Можно посмотреть дом",
    )
    req = store.create(
        object_id="A_1",
        client_session_chat_id="tg_1",
        channel="airbnb_messages",
        source="AIRBNB",
        source_url=listing.source_url,
        outbound_message=plan.first_message,
    )
    store.mark_sent(req.owner_request_id, external_thread_id="ab-thread-1")
    store.mark_awaiting(req.owner_request_id)
    ab = AirbnbMessagesOwnerTransport(
        auth_probe=lambda: AuthStatus.READY,
        navigator=_airbnb_ok_nav,
        rate_guard=AirbnbOwnerMessageRateGuard(path=tmp_path / "rg2.json"),
    )
    d = dispatch_outreach_plan(
        plan,
        client_session_chat_id="tg_1",
        request_store=store,
        dry_run=True,
        airbnb_transport=ab,
    )
    assert d.duplicate_blocked is True
    assert d.result.outcome == SendOutcome.DUPLICATE
