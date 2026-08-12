"""Owner channel resolver: WA → TG → SOURCE_NATIVE priority + FB policy."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.models import LeadProfile, Listing, OwnerChannel
from agent7_envoy.channel_resolver import (
    OwnerMessagingChannel,
    resolve_owner_channel,
)
from agent7_envoy.outreach import build_outreach_plan
from agent7.airbnb_check import CalendarCheck


def make_listing(**kw) -> Listing:
    base = dict(object_id="20260701_001", title="Вилла", price_month=50000)
    base.update(kw)
    return Listing(**base)


def checker_open(url, ci, co):
    return CalendarCheck(available=True, blocked_ranges=[])


def test_1_wa_present_selects_whatsapp():
    l = make_listing(
        owner_whatsapp="+66812345678",
        owner_telegram="@own",
        source_url="https://www.facebook.com/marketplace/item/1",
    )
    d = resolve_owner_channel(l)
    assert d.channel == OwnerMessagingChannel.WHATSAPP
    assert d.destination == "+66812345678"


def test_2_wa_absent_tg_present_selects_telegram():
    l = make_listing(
        owner_whatsapp="",
        owner_telegram="@owner_tg",
        source_url="https://www.airbnb.com/rooms/1",
    )
    d = resolve_owner_channel(l)
    assert d.channel == OwnerMessagingChannel.TELEGRAM
    assert d.destination == "@owner_tg"


def test_3_both_absent_facebook_selects_messenger():
    lead = LeadProfile(stay_months=6, check_in=date(2026, 8, 1), check_out=date(2027, 2, 1))
    l = make_listing(source_url="https://www.facebook.com/marketplace/item/9")
    d = resolve_owner_channel(l, lead)
    assert d.channel == OwnerMessagingChannel.FACEBOOK_MESSENGER
    assert d.destination.endswith("/item/9")
    assert d.owner_channel == OwnerChannel.FACEBOOK_MESSENGER


def test_4_both_absent_airbnb_selects_airbnb_messages():
    l = make_listing(source_url="https://www.airbnb.com/rooms/42")
    d = resolve_owner_channel(l)
    assert d.channel == OwnerMessagingChannel.AIRBNB_MESSAGES
    assert "airbnb.com/rooms/42" in d.destination


def test_5_unknown_source_none():
    l = make_listing(source_url="https://example.com/listing/1")
    d = resolve_owner_channel(l)
    assert d.channel == OwnerMessagingChannel.NONE
    assert "OWNER_CONTACT_UNAVAILABLE" in d.reason


def test_6_invalid_wa_valid_tg_selects_telegram():
    l = make_listing(
        owner_whatsapp="n/a",
        owner_telegram="@valid_owner",
        source_url="https://www.airbnb.com/rooms/1",
    )
    d = resolve_owner_channel(l)
    assert d.channel == OwnerMessagingChannel.TELEGRAM
    assert "whatsapp_invalid" in d.reason


def test_7_facebook_short_stay_blocks_outreach():
    lead = LeadProfile(
        stay_months=3,
        check_in=date(2026, 8, 1),
        check_out=date(2026, 11, 1),
        guests=2,
    )
    l = make_listing(source_url="https://www.facebook.com/marketplace/item/9")
    d = resolve_owner_channel(l, lead)
    assert d.channel == OwnerMessagingChannel.NONE
    assert d.policy_blocked is True
    plan = build_outreach_plan(l, lead, checker=checker_open)
    assert plan.channel is None
    assert "6 месяцев" in plan.skip_reason or "facebook" in plan.skip_reason.lower()


def test_8_facebook_ge6_messenger_eligible():
    lead = LeadProfile(
        stay_months=6,
        check_in=date(2026, 8, 1),
        check_out=date(2027, 2, 1),
        guests=2,
    )
    l = make_listing(source_url="https://www.facebook.com/marketplace/item/9")
    d = resolve_owner_channel(l, lead)
    assert d.channel == OwnerMessagingChannel.FACEBOOK_MESSENGER
    plan = build_outreach_plan(l, lead, checker=checker_open)
    assert plan.channel == OwnerChannel.FACEBOOK_MESSENGER
    assert plan.first_message
    assert "WhatsApp" in plan.first_message


def test_9_airbnb_long_term_eligible():
    lead = LeadProfile(
        stay_months=8,
        check_in=date(2026, 8, 1),
        check_out=date(2027, 4, 1),
        guests=2,
    )
    l = make_listing(source_url="https://www.airbnb.com/rooms/99")
    d = resolve_owner_channel(l, lead)
    assert d.channel == OwnerMessagingChannel.AIRBNB_MESSAGES
    plan = build_outreach_plan(l, lead, checker=checker_open)
    assert plan.channel == OwnerChannel.AIRBNB_MESSAGES
    assert "Можно посмотреть дом" in plan.first_message


def test_listing_owner_channel_uses_resolver():
    l = make_listing(
        owner_whatsapp="+66123",
        owner_telegram="@own",
        source_url="https://airbnb.com/rooms/1",
    )
    assert l.owner_channel() == (OwnerChannel.WHATSAPP, "+66123")
