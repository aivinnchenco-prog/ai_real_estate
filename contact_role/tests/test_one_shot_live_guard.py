"""One-shot live guard tests — no real writes, no live event."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contact_role" / "src"))
sys.path.insert(0, str(ROOT / "whatsapp_ui_sync" / "src"))
sys.path.insert(0, str(ROOT / "agent_6_qualifier" / "src"))

from contact_role.assign import assign_known_role
from contact_role.one_shot import (
    VINCHENCO_CONTACT_ID,
    VINCHENCO_PHONE,
    OneShotLiveGuard,
)
from contact_role.roles import CanonicalRole
from contact_role.sources import RoleSource
from contact_role.state import ContactRoleStore
from contact_role.sync.amocrm import AmoContactRoleSync, FakeAmoContactClient, contact_with_phone
from contact_role.sync.coordinator import ContactRoleSyncCoordinator
from contact_role.sync.outbox import ContactRoleSyncOutbox


@pytest.fixture
def store(tmp_path):
    return ContactRoleStore(tmp_path / "roles.json")


@pytest.fixture
def guard(tmp_path):
    g = OneShotLiveGuard(
        state_path=tmp_path / "one_shot_state.json",
        report_path=tmp_path / "one_shot_live_event_report.json",
    )
    g.reset_for_tests()
    return g


@pytest.fixture
def coord(tmp_path, store):
    return ContactRoleSyncCoordinator(
        store=store,
        outbox=ContactRoleSyncOutbox(tmp_path / "jobs.json"),
        amo_sync=AmoContactRoleSync(
            FakeAmoContactClient([contact_with_phone(9, "+66625124901")]),
            dry_run=True,
            enabled=False,
        ),
        process_inline_dry_run=True,
        process_amo_async=False,
    )


@pytest.fixture
def arm_one_shot(monkeypatch):
    monkeypatch.setenv("CONTACT_ROLE_AUTO_ASSIGN_ENABLED", "true")
    monkeypatch.setenv("CONTACT_ROLE_ONE_SHOT_LIVE_TEST", "true")
    monkeypatch.setenv("CONTACT_ROLE_AMO_SYNC_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_UI_SYNC_ENABLED", "false")


def _listing(phone: str, typ: str, object_id: str = "obj1"):
    from agent6_qualifier.models import Listing

    return Listing(
        object_id=object_id,
        owner_whatsapp=phone,
        owner_agent_type=typ,
    )


def test_01_first_new_owner_event_qualifies(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    r = on_contact_outreach_started(
        listing=_listing("+66625124901", "Владелец"),
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        force=False,
    )
    assert r.canonical_role == "OWNER"
    assert r.role_source == "AGENT7_OUTREACH"
    assert r.route == "AGENT_7"
    assert r.metadata.get("role_assigned_before_outbound") is True
    assert r.metadata.get("one_shot_consumed") is True
    assert guard.load().consumed is True
    assert guard.report_path.exists()


def test_02_first_new_agent_event_qualifies(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    r = on_contact_outreach_started(
        listing=_listing("+66625124902", "Агент"),
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        force=False,
    )
    assert r.canonical_role == "AGENT"
    assert r.route == "AGENT_7"
    assert guard.load().consumed is True
    report = json.loads(guard.report_path.read_text(encoding="utf-8"))
    assert report["role"] == "AGENT"
    assert report["role_assigned_before_outbound"] is True


def test_03_empty_type_ignored(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    r = on_contact_outreach_started(
        listing=_listing("+66625124903", ""),
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        force=False,
    )
    assert r["reason"] == "empty_notion_type_no_explicit_workflow"
    assert guard.load().consumed is False
    assert store.get_by_phone("+66625124903") is None


def test_04_client_ignored(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_client_conversation_started

    r = on_client_conversation_started(
        phone="+66625124904",
        store=store,
        coordinator=coord,
        force=False,
    )
    assert r["reason"] == "one_shot_agent7_only"
    assert guard.load().consumed is False
    assert store.get_by_phone("+66625124904") is None


def test_05_unknown_ignored(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_unknown_inbound

    r = on_unknown_inbound(
        phone="+66625124905",
        text="бюджет до 80000 bang tao на месяц",
        store=store,
        coordinator=coord,
        force=False,
    )
    assert r["reason"] == "one_shot_agent7_only"
    assert guard.load().consumed is False


def test_06_old_event_before_activation_ignored(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    activated = guard.ensure_activated()
    old = (
        datetime.fromisoformat(activated.activated_at.replace("Z", "+00:00"))
        - timedelta(hours=1)
    ).isoformat()

    r = on_contact_outreach_started(
        listing=_listing("+66625124906", "Владелец"),
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        event_at=old,
        force=False,
    )
    assert r["reason"] == "event_before_activation"
    assert guard.load().consumed is False
    assert store.get_by_phone("+66625124906") is None


def test_07_conflicting_manual_lock_does_not_consume(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    assign_known_role(
        phone="+66625124907",
        role=CanonicalRole.CLIENT,
        source=RoleSource.MANUAL,
        store=store,
        coordinator=coord,
    )
    r = on_contact_outreach_started(
        listing=_listing("+66625124907", "Владелец"),
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        force=False,
    )
    assert r["reason"] == "priority_blocked" or (
        hasattr(r, "reason") and r.reason == "priority_blocked"
    )
    assert guard.load().consumed is False
    assert store.get_by_phone("+66625124907").canonical_role == "CLIENT"
    assert store.get_by_phone("+66625124907").locked_by_manual_override is True


def test_08_09_accepted_consumes_second_blocked(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    r1 = on_contact_outreach_started(
        listing=_listing("+66625124908", "Владелец"),
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        force=False,
    )
    assert r1.metadata.get("one_shot_consumed") is True

    r2 = on_contact_outreach_started(
        listing=_listing("+66625124909", "Агент"),
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        force=False,
    )
    assert r2["reason"] == "one_shot_consumed"
    assert store.get_by_phone("+66625124909") is None


def test_10_assignment_before_outbound(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    outbound_sent = False

    r = on_contact_outreach_started(
        listing=_listing("+66625124910", "Владелец"),
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        outbound_status="NOT_SENT",
        force=False,
    )
    assert r.canonical_role == "OWNER"
    assert r.metadata["role_assigned_before_outbound"] is True
    assert outbound_sent is False
    report = json.loads(guard.report_path.read_text(encoding="utf-8"))
    assert report["role_assigned_before_outbound"] is True
    assert report["outbound_status"] == "NOT_SENT"


def test_11_12_mirror_failure_no_rollback(store, tmp_path, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    class Boom:
        def fan_out(self, *a, **k):
            raise RuntimeError("mirror down")

    r = on_contact_outreach_started(
        listing=_listing("+66625124911", "Владелец"),
        store=store,
        coordinator=Boom(),
        one_shot_guard=guard,
        force=False,
    )
    assert r.canonical_role == "OWNER"
    assert r.route == "AGENT_7"
    assert store.get_by_phone("+66625124911").canonical_role == "OWNER"
    assert r.sync.amocrm_code == "FAILED"
    assert guard.load().consumed is True
    report = json.loads(guard.report_path.read_text(encoding="utf-8"))
    assert report["amo_status"] == "FAILED"
    assert report["wa_status"] == "FAILED"


def test_13_report_generated(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    on_contact_outreach_started(
        listing=_listing("+66625124912", "Владелец", object_id="o12"),
        contact_id=999001,
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        force=False,
    )
    report = json.loads(guard.report_path.read_text(encoding="utf-8"))
    for key in (
        "event_id",
        "timestamp",
        "contact_id",
        "masked_phone",
        "role",
        "role_source",
        "trigger",
        "role_assigned_before_outbound",
        "routing",
        "amo_status",
        "wa_status",
        "outbound_status",
        "one_shot_consumed",
    ):
        assert key in report
    assert report["contact_id"] == 999001
    assert report["role"] == "OWNER"
    assert report["role_source"] == "AGENT7_OUTREACH"
    assert report["trigger"] == "CONTACT_OUTREACH_STARTED"
    assert report["one_shot_consumed"] is True
    assert "token" not in json.dumps(report).lower()
    assert "secret" not in json.dumps(report).lower()


def test_14_no_backfill():
    import contact_role.one_shot as mod
    import ast

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    # No loops over contacts / bulk scan helpers.
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "for all contacts" not in src.lower()
    assert "scan_all" not in src.lower()
    assert "backfill_all" not in src.lower()
    fn_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "backfill" not in {n.lower() for n in fn_names}


def test_15_vinchenco_client_manual_untouched(store, coord, guard, arm_one_shot):
    from agent6_qualifier.messaging.contact_role_hook import on_contact_outreach_started

    # Seed Vinchenco as CLIENT MANUAL (must not change).
    assign_known_role(
        phone=VINCHENCO_PHONE,
        role=CanonicalRole.CLIENT,
        source=RoleSource.MANUAL,
        contact_id=VINCHENCO_CONTACT_ID,
        store=store,
        coordinator=coord,
    )
    vin = store.get_by_phone(VINCHENCO_PHONE)
    assert vin.canonical_role == "CLIENT"
    assert vin.locked_by_manual_override is True

    # Conflicting OWNER attempt on Vinchenco does not consume.
    blocked = on_contact_outreach_started(
        listing=_listing(VINCHENCO_PHONE, "Владелец"),
        contact_id=VINCHENCO_CONTACT_ID,
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        force=False,
    )
    assert (
        blocked.get("reason") == "priority_blocked"
        if isinstance(blocked, dict)
        else blocked.reason == "priority_blocked"
    )
    assert guard.load().consumed is False

    # A different new OWNER contact can still consume the one-shot.
    r = on_contact_outreach_started(
        listing=_listing("+66625124915", "Владелец"),
        store=store,
        coordinator=coord,
        one_shot_guard=guard,
        force=False,
    )
    assert r.canonical_role == "OWNER"
    vin2 = store.get_by_phone(VINCHENCO_PHONE)
    assert vin2.canonical_role == "CLIENT"
    assert vin2.role_source == "MANUAL"
    assert vin2.locked_by_manual_override is True
    assert vin2.metadata.get("contact_id") == VINCHENCO_CONTACT_ID


def test_flags_default_false():
    from contact_role.flags import (
        amo_sync_enabled,
        auto_assign_enabled,
        one_shot_live_test_enabled,
        whatsapp_ui_sync_enabled,
    )

    # Unset in this process may still inherit env — check defaults via helper semantics
    assert callable(one_shot_live_test_enabled)
    # With env cleared:
    import os

    for k in (
        "CONTACT_ROLE_AUTO_ASSIGN_ENABLED",
        "CONTACT_ROLE_ONE_SHOT_LIVE_TEST",
        "CONTACT_ROLE_AMO_SYNC_ENABLED",
        "WHATSAPP_UI_SYNC_ENABLED",
    ):
        os.environ.pop(k, None)
    assert auto_assign_enabled() is False
    assert one_shot_live_test_enabled() is False
    assert amo_sync_enabled() is False
    assert whatsapp_ui_sync_enabled() is False
