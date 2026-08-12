"""Offline tests: role sources, priority, Agent 7 outreach before inbound."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contact_role" / "src"))
sys.path.insert(0, str(ROOT / "whatsapp_ui_sync" / "src"))
sys.path.insert(0, str(ROOT / "agent_6_qualifier" / "src"))

from contact_role.assign import assign_known_role, unlock_manual_override
from contact_role.classify import classify_unknown_contact_role
from contact_role.mapping import role_from_notion_owner_agent_type, whatsapp_list_for_role
from contact_role.roles import CanonicalRole
from contact_role.router import RouteTarget, route_contact, route_for_role
from contact_role.sources import RoleSource
from contact_role.state import ContactRoleStore
from contact_role.sync.amocrm import AmoContactRoleSync, FakeAmoContactClient, contact_with_phone
from contact_role.sync.coordinator import ContactRoleSyncCoordinator
from contact_role.sync.outbox import ContactRoleSyncOutbox


@pytest.fixture
def store(tmp_path):
    return ContactRoleStore(tmp_path / "roles.json")


@pytest.fixture
def coordinator(tmp_path, store):
    amo = AmoContactRoleSync(
        FakeAmoContactClient(
            [contact_with_phone(1, "+66625124001", tags=[], type_value="")]
        ),
        dry_run=True,
        enabled=False,
    )
    return ContactRoleSyncCoordinator(
        store=store,
        outbox=ContactRoleSyncOutbox(tmp_path / "jobs.json"),
        amo_sync=amo,
        process_inline_dry_run=True,
    )


def test_01_agent7_owner_outreach_before_inbound(store, coordinator):
    r = assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coordinator,
    )
    assert r.applied and r.changed
    assert r.canonical_role == "OWNER"
    assert r.role_source == "AGENT7_OUTREACH"
    assert r.route == RouteTarget.AGENT_7.value
    assert store.get_by_phone("+66625124001").canonical_role == "OWNER"


def test_02_agent7_agent_outreach_before_inbound(store, coordinator):
    r = assign_known_role(
        phone="+66625124002",
        role=CanonicalRole.AGENT,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coordinator,
    )
    assert r.canonical_role == "AGENT"
    assert whatsapp_list_for_role(r.canonical_role) == "Owner"
    assert r.route == RouteTarget.AGENT_7.value


def test_03_known_owner_beats_message_classification(store, coordinator):
    assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coordinator,
    )
    c = classify_unknown_contact_role(
        phone="+66625124001",
        text="Ищу квартиру в Bang Tao до 80000",
        store=store,
        enqueue_sync=True,
    )
    assert c.reason == "already_known_skip_classifier"
    assert store.get_by_phone("+66625124001").canonical_role == "OWNER"


def test_04_known_agent_beats_message_classification(store, coordinator):
    assign_known_role(
        phone="+66625124003",
        role=CanonicalRole.AGENT,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coordinator,
    )
    blocked = assign_known_role(
        phone="+66625124003",
        role=CanonicalRole.CLIENT,
        source=RoleSource.MESSAGE_CLASSIFICATION,
        store=store,
        coordinator=coordinator,
    )
    assert not blocked.applied
    assert blocked.reason == "priority_blocked"
    assert blocked.canonical_role == "AGENT"


def test_05_manual_beats_all_auto(store, coordinator):
    assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.MANUAL,
        store=store,
        coordinator=coordinator,
    )
    blocked = assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.CLIENT,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coordinator,
    )
    assert blocked.reason == "priority_blocked"
    assert store.get_by_phone("+66625124001").canonical_role == "OWNER"


def test_06_unknown_inbound_client(store, coordinator):
    c = classify_unknown_contact_role(
        phone="+66625124901",
        text="Здравствуйте, ищу виллу на месяц",
        store=store,
    )
    assert c.candidate_role == "CLIENT"
    assert c.applied and c.applied.changed
    assert c.applied.route == RouteTarget.AGENT_6.value


def test_07_unknown_inbound_owner(store, coordinator):
    c = classify_unknown_contact_role(
        phone="+66625124902",
        text="У меня есть вилла в Bang Tao, хочу сдать",
        store=store,
    )
    assert c.candidate_role == "OWNER"
    assert c.applied.route == RouteTarget.AGENT_7.value


def test_08_unknown_inbound_agent(store, coordinator):
    c = classify_unknown_contact_role(
        phone="+66625124903",
        text="Я агент, есть объект от собственника",
        store=store,
    )
    assert c.candidate_role == "AGENT"


def test_09_confirmed_owner_not_overwritten_by_weak_client_msg(store, coordinator):
    assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coordinator,
    )
    c = classify_unknown_contact_role(
        phone="+66625124001",
        text="ок, бюджет до 50",
        store=store,
    )
    assert store.get_by_phone("+66625124001").canonical_role == "OWNER"
    assert c.reason in {"already_known_skip_classifier", "priority_blocked", "ambiguous", "empty"} or (
        c.applied is None or not c.applied.changed
    )


def test_10_confirmed_client_not_overwritten_by_weak_owner_msg(store, coordinator):
    assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.CLIENT,
        source=RoleSource.INBOUND_LEAD,
        store=store,
        coordinator=coordinator,
    )
    blocked = assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.MESSAGE_CLASSIFICATION,
        store=store,
        coordinator=coordinator,
    )
    assert blocked.reason == "priority_blocked"
    assert blocked.canonical_role == "CLIENT"


def test_notion_type_mapping():
    assert role_from_notion_owner_agent_type("Владелец") is CanonicalRole.OWNER
    assert role_from_notion_owner_agent_type("Агент") is CanonicalRole.AGENT
    assert role_from_notion_owner_agent_type("") is CanonicalRole.UNKNOWN


def test_routing_targets():
    assert route_for_role(CanonicalRole.CLIENT) is RouteTarget.AGENT_6
    assert route_for_role(CanonicalRole.OWNER) is RouteTarget.AGENT_7
    assert route_for_role(CanonicalRole.AGENT) is RouteTarget.AGENT_7
    assert route_for_role(CanonicalRole.UNKNOWN) is RouteTarget.CLASSIFIER


def test_router_uses_existing(store, coordinator):
    assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coordinator,
    )
    d = route_contact(phone="+66625124001", text="ищу виллу", store=store)
    assert d.used_existing
    assert d.route == "AGENT_7"
    assert d.canonical_role == "OWNER"


def test_manual_unlock(store, coordinator):
    assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.MANUAL,
        store=store,
        coordinator=coordinator,
    )
    unlock_manual_override(phone="+66625124001", store=store)
    # Still MANUAL source but unlocked — AGENT7 still cannot overwrite (lower? equal workflow)
    # MANUAL rank 100 > AGENT7 80, so still blocked unless allow_reevaluation with higher.
    # After unlock, locked flag false but source still MANUAL — priority still blocks.
    assert store.get_by_phone("+66625124001").locked_by_manual_override is False


def test_agent7_hook_notion_type_and_empty_policy(tmp_path, store, coordinator):
    from agent6_qualifier.messaging.contact_role_hook import assign_agent7_outreach_role
    from agent6_qualifier.models import Listing
    from contact_role.router import route_for_role
    from contact_role.roles import CanonicalRole

    listing = Listing(
        object_id="20260809_001",
        owner_whatsapp="+66625124001",
        owner_agent_type="Владелец",
    )
    r = assign_agent7_outreach_role(
        listing=listing, store=store, coordinator=coordinator, force=True
    )
    assert r is not None
    assert r.canonical_role == "OWNER"
    assert r.role_source == "AGENT7_OUTREACH"

    listing_agent = Listing(
        object_id="20260809_002",
        owner_whatsapp="+66625124002",
        owner_agent_type="Агент",
    )
    r2 = assign_agent7_outreach_role(
        listing=listing_agent, store=store, coordinator=coordinator, force=True
    )
    assert r2.canonical_role == "AGENT"

    # empty Notion type alone → UNKNOWN, does NOT silently become OWNER
    listing_empty = Listing(
        object_id="20260809_003",
        owner_whatsapp="+66625124003",
        owner_agent_type="",
    )
    r3 = assign_agent7_outreach_role(
        listing=listing_empty, store=store, coordinator=coordinator, force=True
    )
    assert r3["canonical_role"] == "UNKNOWN"
    assert r3["applied"] is False
    assert r3["reason"] == "empty_notion_type_no_explicit_workflow"
    assert r3["route"] == route_for_role(CanonicalRole.UNKNOWN).value
    assert r3["route"] == "CLASSIFIER"
    assert store.get_by_phone("+66625124003") is None

    # empty + explicit owner workflow → OWNER before reply
    r4 = assign_agent7_outreach_role(
        listing=listing_empty,
        explicit_role="OWNER",
        store=store,
        coordinator=coordinator,
        force=True,
    )
    assert r4.canonical_role == "OWNER"
    assert r4.role_source == "AGENT7_OUTREACH"
    assert r4.route == "AGENT_7"

    # empty + explicit agent workflow → AGENT before reply
    listing_empty2 = Listing(
        object_id="20260809_004",
        owner_whatsapp="+66625124004",
        owner_agent_type="",
    )
    r5 = assign_agent7_outreach_role(
        listing=listing_empty2,
        explicit_role="AGENT",
        store=store,
        coordinator=coordinator,
        force=True,
    )
    assert r5.canonical_role == "AGENT"
    assert r5.route == "AGENT_7"


def test_empty_notion_type_mapping_is_unknown():
    assert role_from_notion_owner_agent_type("") is CanonicalRole.UNKNOWN
    assert role_from_notion_owner_agent_type(None) is CanonicalRole.UNKNOWN
    assert role_from_notion_owner_agent_type("   ") is CanonicalRole.UNKNOWN
