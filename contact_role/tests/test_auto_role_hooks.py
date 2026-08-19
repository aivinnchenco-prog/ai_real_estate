"""Automation wiring tests: Agent7/6 hooks, flags, isolation, no backfill."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contact_role" / "src"))
sys.path.insert(0, str(ROOT / "whatsapp_ui_sync" / "src"))
sys.path.insert(0, str(ROOT / "agent_6_qualifier" / "src"))

from contact_role.assign import assign_known_role
from contact_role.audit import AuditLog
from contact_role.classify import classify_unknown_contact_role
from contact_role.roles import CanonicalRole
from contact_role.router import route_contact, route_for_role
from contact_role.sources import RoleSource
from contact_role.state import ContactRoleStore
from contact_role.sync.amocrm import AmoContactRoleSync, FakeAmoContactClient, contact_with_phone
from contact_role.sync.coordinator import ContactRoleSyncCoordinator
from contact_role.sync.outbox import ContactRoleSyncOutbox
from whatsapp_ui_sync.selectors import SELECTORS


@pytest.fixture
def store(tmp_path):
    return ContactRoleStore(tmp_path / "roles.json")


@pytest.fixture
def coord(tmp_path, store):
    return ContactRoleSyncCoordinator(
        store=store,
        outbox=ContactRoleSyncOutbox(tmp_path / "jobs.json"),
        amo_sync=AmoContactRoleSync(
            FakeAmoContactClient([contact_with_phone(1, "+66625124001")]),
            dry_run=True,
            enabled=False,
        ),
        process_inline_dry_run=True,
        process_amo_async=False,
    )


def test_01_02_agent7_owner_agent_before_outbound(store, coord):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started
    from agent6_qualifier.models import Listing

    owner = Listing(
        object_id="o1", owner_whatsapp="+66625124001", owner_agent_type="Владелец"
    )
    r = on_contact_outreach_started(
        listing=owner, store=store, coordinator=coord, force=True
    )
    assert r.canonical_role == "OWNER"
    assert r.role_source == "AGENT7_OUTREACH"
    assert r.route == "AGENT_7"

    agent = Listing(
        object_id="a1", owner_whatsapp="+66625124002", owner_agent_type="Агент"
    )
    r2 = on_contact_outreach_started(
        listing=agent, store=store, coordinator=coord, force=True
    )
    assert r2.canonical_role == "AGENT"
    assert r2.route == "AGENT_7"


def test_03_empty_type_unknown(store, coord):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started
    from agent6_qualifier.models import Listing

    empty = Listing(object_id="e1", owner_whatsapp="+66625124003", owner_agent_type="")
    r = on_contact_outreach_started(
        listing=empty, store=store, coordinator=coord, force=True
    )
    assert r["canonical_role"] == "UNKNOWN"
    assert r["applied"] is False


def test_04_manual_lock_blocks_workflow(store, coord, tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.CLIENT,
        source=RoleSource.MANUAL,
        store=store,
        coordinator=coord,
        audit=audit,
    )
    blocked = assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coord,
        audit=audit,
        trigger="CONTACT_OUTREACH_STARTED",
    )
    assert blocked.reason == "priority_blocked"
    assert any(
        e.event == "ROLE_ASSIGNMENT_REJECTED_BY_PRIORITY" for e in audit.events
    )


def test_05_client_known_lead_bypasses_classifier(store, coord):
    from agent6_qualifier.messaging.contact_role_hook import (
        is_confirmed_client_context,
        on_client_conversation_started,
        on_unknown_inbound,
    )

    assert is_confirmed_client_context(known_client=True)
    assert is_confirmed_client_context(source_hint="INBOUND_LEAD")
    assert is_confirmed_client_context(
        text="Здравствуйте, интересует объект F_20260809_001"
    )
    assert is_confirmed_client_context(text="Интересует объект")
    assert is_confirmed_client_context(text="смотрите A_20260713_003")
    assert not is_confirmed_client_context(text="привет")

    r = on_client_conversation_started(
        phone="+66625124910",
        source="INBOUND_LEAD",
        store=store,
        coordinator=coord,
        force=True,
    )
    assert r.canonical_role == "CLIENT"
    assert r.route == "AGENT_6"
    # classifier should skip
    c = classify_unknown_contact_role(
        phone="+66625124910",
        text="ищу виллу",
        store=store,
    )
    assert c.reason == "already_known_skip_classifier"

    # Confirmed rental inquiry via on_unknown_inbound also bypasses classifier path
    r2 = on_unknown_inbound(
        phone="+66625124920",
        text="Хочу снять виллу на месяц",
        known_client=True,
        store=store,
        coordinator=coord,
        force=True,
    )
    assert r2.canonical_role == "CLIENT"
    assert r2.role_source in {"AGENT6_INBOUND", "INBOUND_LEAD"}
    assert r2.route == "AGENT_6"

    # Agent 6 style object inquiry + ID → CLIENT without villa keywords
    r3 = on_unknown_inbound(
        phone="+66625124921",
        text="Здравствуйте, интересует объект F_20260809_001",
        store=store,
        coordinator=coord,
        force=True,
    )
    assert r3.canonical_role == "CLIENT"
    assert r3.route == "AGENT_6"
    assert r3.role_source in {"AGENT6_INBOUND", "INBOUND_LEAD", "MESSAGE_CLASSIFICATION"}


def test_06_07_08_09_unknown_classifier_routes(store, coord):
    from agent6_qualifier.messaging.contact_role_hook import on_unknown_inbound

    # Text matches classifier CLIENT keywords but NOT confirmed-lead shortcut markers.
    client = on_unknown_inbound(
        phone="+66625124911",
        text="Здравствуйте, бюджет до 80000 bang tao на месяц",
        store=store,
        coordinator=coord,
        force=True,
    )
    payload = client.to_dict() if hasattr(client, "to_dict") else client
    assert payload["canonical_role"] == "CLIENT"
    assert payload["route"] == "AGENT_6"
    assert payload.get("used_classifier") or payload.get("role_source") == "MESSAGE_CLASSIFICATION"

    owner = on_unknown_inbound(
        phone="+66625124912",
        text="У меня есть вилла, хочу сдать",
        store=store,
        coordinator=coord,
        force=True,
    )
    payload_o = owner.to_dict() if hasattr(owner, "to_dict") else owner
    assert payload_o["canonical_role"] == "OWNER"
    assert payload_o["route"] == "AGENT_7"

    agent = on_unknown_inbound(
        phone="+66625124913",
        text="Я агент, есть объект от собственника",
        store=store,
        coordinator=coord,
        force=True,
    )
    payload_a = agent.to_dict() if hasattr(agent, "to_dict") else agent
    assert payload_a["canonical_role"] == "AGENT"
    assert payload_a["route"] == "AGENT_7"


def test_10_same_role_no_duplicate_sync(store, coord):
    r1 = assign_known_role(
        phone="+66625124001",
        role="CLIENT",
        source=RoleSource.INBOUND_LEAD,
        store=store,
        coordinator=coord,
    )
    n1 = len(coord.outbox.list_jobs())
    r2 = assign_known_role(
        phone="+66625124001",
        role="CLIENT",
        source=RoleSource.INBOUND_LEAD,
        store=store,
        coordinator=coord,
    )
    assert r1.changed and not r2.changed
    assert r2.sync and r2.sync.amocrm_code == "ALREADY_SYNCED"
    assert len(coord.outbox.list_jobs()) == n1


def test_11_12_mirror_fail_does_not_block_routing(store, tmp_path):
    # amo missing contact + WA enqueue fail isolated
    coord = ContactRoleSyncCoordinator(
        store=store,
        outbox=ContactRoleSyncOutbox(tmp_path / "jobs.json"),
        amo_sync=AmoContactRoleSync(FakeAmoContactClient([]), dry_run=True, enabled=True),
        process_inline_dry_run=True,
        process_amo_async=False,
    )
    r = assign_known_role(
        phone="+66625124999",
        role="OWNER",
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coord,
    )
    assert r.changed
    assert r.route == "AGENT_7"
    assert store.get_by_phone("+66625124999").canonical_role == "OWNER"


def test_13_14_disabled_mirrors_still_route(store, tmp_path, monkeypatch):
    monkeypatch.setenv("CONTACT_ROLE_AMO_SYNC_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_UI_SYNC_ENABLED", "false")
    coord = ContactRoleSyncCoordinator(
        store=store,
        outbox=ContactRoleSyncOutbox(tmp_path / "jobs2.json"),
        amo_sync=AmoContactRoleSync(FakeAmoContactClient([]), dry_run=True, enabled=False),
        process_inline_dry_run=False,
        process_amo_async=False,
    )
    r = assign_known_role(
        phone="+66625124888",
        role="CLIENT",
        source=RoleSource.INBOUND_LEAD,
        store=store,
        coordinator=coord,
    )
    assert r.route == "AGENT_6"
    assert r.sync.amocrm_code == "DISABLED"
    assert r.sync.whatsapp_code == "DISABLED"


def test_15_16_no_global_backfill_old_untouched(store, coord):
    # Existing unrelated contact stays untouched when another contact is assigned.
    assign_known_role(
        phone="+66625124001",
        role="CLIENT",
        source=RoleSource.MANUAL,
        store=store,
        coordinator=coord,
    )
    assign_known_role(
        phone="+66625124777",
        role="OWNER",
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coord,
    )
    assert store.get_by_phone("+66625124001").canonical_role == "CLIENT"
    assert store.get_by_phone("+66625124777").canonical_role == "OWNER"
    # No scanner / bulk API in package
    import contact_role

    src = Path(contact_role.__file__).read_text(encoding="utf-8")
    assert "backfill" not in src.lower()


def test_17_18_send_not_blocked_by_mirror_failure(store, coord):
    from agent6_qualifier.messaging.contact_role_hook import (
        on_client_conversation_started,
        on_contact_outreach_started,
    )
    from agent6_qualifier.models import Listing

    class Boom:
        def fan_out(self, *a, **k):
            raise RuntimeError("mirror down")

    listing = Listing(
        object_id="x", owner_whatsapp="+66625124666", owner_agent_type="Владелец"
    )
    r = on_contact_outreach_started(
        listing=listing, store=store, coordinator=Boom(), force=True
    )
    # Canonical role committed; mirror failure isolated
    assert r is not None
    assert getattr(r, "canonical_role", None) == "OWNER"
    assert getattr(r, "route", None) == "AGENT_7"
    assert store.get_by_phone("+66625124666").canonical_role == "OWNER"
    assert r.sync is not None
    assert r.sync.amocrm_code == "FAILED"

    r2 = on_client_conversation_started(
        phone="+66625124667",
        store=store,
        coordinator=Boom(),
        force=True,
    )
    assert r2 is not None
    assert r2.canonical_role == "CLIENT"
    assert r2.route == "AGENT_6"
    assert r2.sync.amocrm_code == "FAILED"


def test_19_selector_supports_edit_list():
    assert "Изменить список" in SELECTORS.add_to_list_menu
    assert "Edit list" in SELECTORS.add_to_list_menu
    assert "Добавить в список" in SELECTORS.add_to_list_menu


def test_20_21_wazzup_telegram_unchanged():
    from agent6_qualifier.messaging.wazzup_config import WazzupConfig
    from agent6_qualifier.telegram_folders import CLIENT_FOLDER, OWNER_FOLDER

    assert CLIENT_FOLDER == "Клиент"
    assert OWNER_FOLDER == "Owner"
    assert "channel_id" in WazzupConfig.__dataclass_fields__


def test_22_auto_assign_disabled_by_default(store, coord, monkeypatch):
    monkeypatch.delenv("CONTACT_ROLE_AUTO_ASSIGN_ENABLED", raising=False)
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started
    from agent6_qualifier.models import Listing

    listing = Listing(
        object_id="z", owner_whatsapp="+66625124555", owner_agent_type="Владелец"
    )
    r = on_contact_outreach_started(
        listing=listing, store=store, coordinator=coord, force=False
    )
    assert r["reason"] == "auto_assign_disabled"
    assert store.get_by_phone("+66625124555") is None


def test_routing_targets_canonical():
    assert route_for_role("CLIENT").value == "AGENT_6"
    assert route_for_role("OWNER").value == "AGENT_7"
    assert route_for_role("AGENT").value == "AGENT_7"
    assert route_for_role("UNKNOWN").value == "CLASSIFIER"
