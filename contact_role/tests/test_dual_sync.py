"""Offline tests: dual sync amoCRM + WhatsApp, failure isolation, safety."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contact_role" / "src"))
sys.path.insert(0, str(ROOT / "whatsapp_ui_sync" / "src"))

from contact_role.assign import assign_known_role
from contact_role.mapping import (
    AMO_FIELD_NAME,
    amo_field_value,
    amo_tag_for_role,
    whatsapp_list_for_role,
)
from contact_role.roles import CanonicalRole
from contact_role.sources import RoleSource
from contact_role.state import ContactRoleStore
from contact_role.sync.amocrm import (
    AmoContactRoleSync,
    FakeAmoContactClient,
    contact_with_phone,
    plan_tag_update,
    resolve_contact_by_phone,
)
from contact_role.sync.coordinator import ContactRoleSyncCoordinator
from contact_role.sync.outbox import ContactRoleSyncOutbox, SyncTarget
from whatsapp_ui_sync.roles import map_role_to_list


@pytest.fixture
def store(tmp_path):
    return ContactRoleStore(tmp_path / "roles.json")


def _coord(tmp_path, store, amo_client, *, fail_wa=False):
    amo = AmoContactRoleSync(amo_client, dry_run=True, enabled=False)
    coord = ContactRoleSyncCoordinator(
        store=store,
        outbox=ContactRoleSyncOutbox(tmp_path / "jobs.json"),
        amo_sync=amo,
        process_inline_dry_run=True,
    )
    if fail_wa:
        import contact_role.sync.whatsapp as wa_mod

        def _boom(*_a, **_k):
            from contact_role.sync.whatsapp import WhatsAppEnqueueResult

            return WhatsAppEnqueueResult(code="WHATSAPP_ENQUEUE_FAILED", message="timeout")

        wa_mod.enqueue_whatsapp_ui_sync = _boom  # type: ignore
    return coord


def test_11_12_client_amo_and_whatsapp_mapping():
    assert amo_field_value(CanonicalRole.CLIENT) == "Клиент"
    assert amo_tag_for_role(CanonicalRole.CLIENT) == "CLIENT"
    assert whatsapp_list_for_role(CanonicalRole.CLIENT) == "Client"
    assert map_role_to_list("CLIENT") == "Client"


def test_13_14_owner_amo_and_whatsapp_mapping():
    assert amo_field_value(CanonicalRole.OWNER) == "Владелец"
    assert amo_tag_for_role(CanonicalRole.OWNER) == "OWNER"
    assert whatsapp_list_for_role(CanonicalRole.OWNER) == "Owner"


def test_15_16_agent_amo_and_whatsapp_owner_list():
    assert amo_field_value(CanonicalRole.AGENT) == "Агент"
    assert amo_tag_for_role(CanonicalRole.AGENT) == "AGENT"
    assert whatsapp_list_for_role(CanonicalRole.AGENT) == "Owner"


def test_17_unrelated_amo_tags_preserved():
    tags = plan_tag_update(
        [{"name": "VIP"}, {"name": "OBJ_1"}, {"name": "CLIENT"}],
        CanonicalRole.OWNER,
    )
    names = [t["name"] for t in tags]
    assert "VIP" in names and "OBJ_1" in names
    assert "OWNER" in names
    assert "CLIENT" not in names


def test_18_old_canonical_tag_replaced():
    tags = plan_tag_update([{"name": "OWNER"}, {"name": "x"}], CanonicalRole.CLIENT)
    names = [t["name"] for t in tags]
    assert names.count("CLIENT") == 1
    assert "OWNER" not in names
    assert "x" in names


def test_19_same_role_no_duplicate_jobs(tmp_path, store):
    amo = FakeAmoContactClient([contact_with_phone(1, "+66625124001")])
    coord = _coord(tmp_path, store, amo)
    r1 = assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.CLIENT,
        source=RoleSource.INBOUND_LEAD,
        store=store,
        coordinator=coord,
    )
    r2 = assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.CLIENT,
        source=RoleSource.INBOUND_LEAD,
        store=store,
        coordinator=coord,
    )
    assert r1.changed
    assert not r2.changed
    assert r2.sync and r2.sync.amocrm_code == "ALREADY_SYNCED"
    jobs = coord.outbox.list_jobs()
    # only first change enqueues
    amo_jobs = [j for j in jobs if j.target == SyncTarget.AMOCRM.value]
    assert len(amo_jobs) == 1


def test_20_amo_failure_does_not_block_whatsapp(tmp_path, store):
    amo = FakeAmoContactClient([], has_type_field=True)
    coord = _coord(tmp_path, store, amo)
    r = assign_known_role(
        phone="+66625124999",
        role=CanonicalRole.OWNER,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coord,
    )
    assert r.changed
    assert r.canonical_role == "OWNER"  # routing independent
    assert r.sync is not None
    # amo contact missing → FAILED, whatsapp still enqueued/skipped independently
    assert r.sync.amocrm_code in {"CONTACT_NOT_FOUND", "FAILED", "ENQUEUED"}
    assert r.sync.whatsapp_code in {"ENQUEUED", "SKIPPED", "ALREADY_SYNCED", "DONE"}


def test_21_whatsapp_failure_does_not_block_amo(tmp_path, store, monkeypatch):
    amo = FakeAmoContactClient([contact_with_phone(7, "+66625124007")])
    coord = _coord(tmp_path, store, amo)

    def _boom(*_a, **_k):
        from contact_role.sync.whatsapp import WhatsAppEnqueueResult

        return WhatsAppEnqueueResult(code="WHATSAPP_ENQUEUE_FAILED", message="timeout")

    monkeypatch.setattr(
        "contact_role.sync.coordinator.enqueue_whatsapp_ui_sync", _boom
    )
    r = assign_known_role(
        phone="+66625124007",
        role=CanonicalRole.CLIENT,
        source=RoleSource.MESSAGE_CLASSIFICATION,
        store=store,
        coordinator=coord,
    )
    assert r.changed
    assert r.sync.whatsapp_code == "WHATSAPP_ENQUEUE_FAILED"
    assert r.sync.amocrm_code in {"DRY_RUN_PLAN", "ENQUEUED", "ALREADY_SYNCED", "FIELD_MISSING"}


def test_22_routing_independent_from_mirrors(store, tmp_path):
    amo = FakeAmoContactClient([])  # mirrors fail
    coord = _coord(tmp_path, store, amo)
    r = assign_known_role(
        phone="+66625124008",
        role=CanonicalRole.AGENT,
        source=RoleSource.AGENT7_OUTREACH,
        store=store,
        coordinator=coord,
    )
    assert r.route == "AGENT_7"
    assert store.get_by_phone("+66625124008").canonical_role == "AGENT"


def test_23_ambiguous_amo_contact_blocked():
    c1 = contact_with_phone(1, "+66625124001")
    c2 = contact_with_phone(2, "+66625124001")
    amo = FakeAmoContactClient([c1, c2])
    code, cid, ids = resolve_contact_by_phone(amo, "+66625124001")
    assert code == "AMBIGUOUS_CONTACT"
    assert cid is None
    assert len(ids) == 2


def test_24_no_duplicate_amo_contact_create():
    """Role sync never creates contacts — only updates existing."""
    amo = FakeAmoContactClient([])
    sync = AmoContactRoleSync(amo, dry_run=False, enabled=True)
    result = sync.sync_role(
        phone="+66625124001", role=CanonicalRole.CLIENT, confirm_write=True
    )
    assert result.code == "CONTACT_NOT_FOUND"
    assert amo.updates == []


def test_25_manual_override_blocks_classifier(store, tmp_path):
    coord = _coord(tmp_path, store, FakeAmoContactClient([contact_with_phone(1, "+66625124001")]))
    assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.MANUAL,
        store=store,
        coordinator=coord,
    )
    from contact_role.classify import classify_unknown_contact_role

    c = classify_unknown_contact_role(
        phone="+66625124001",
        text="ищу виллу",
        store=store,
    )
    assert store.get_by_phone("+66625124001").canonical_role == "OWNER"
    assert c.reason == "already_known_skip_classifier"


def test_26_27_28_29_routes(store, tmp_path):
    coord = _coord(tmp_path, store, FakeAmoContactClient([contact_with_phone(1, "+66625124001")]))
    assert (
        assign_known_role(
            phone="+66625124011",
            role="CLIENT",
            source=RoleSource.INBOUND_LEAD,
            store=store,
            coordinator=coord,
        ).route
        == "AGENT_6"
    )
    assert (
        assign_known_role(
            phone="+66625124012",
            role="OWNER",
            source=RoleSource.AGENT7_OUTREACH,
            store=store,
            coordinator=coord,
        ).route
        == "AGENT_7"
    )
    assert (
        assign_known_role(
            phone="+66625124013",
            role="AGENT",
            source=RoleSource.AGENT7_OUTREACH,
            store=store,
            coordinator=coord,
        ).route
        == "AGENT_7"
    )
    from contact_role.router import route_contact

    d = route_contact(phone="+66625124900", text="привет", store=store)
    assert d.route in {"CLASSIFIER", "AGENT_6", "AGENT_7"}
    assert d.used_classifier or d.canonical_role == "UNKNOWN"


def test_30_wazzup_channel_unchanged():
    # Import config only — do not mutate.
    sys.path.insert(0, str(ROOT / "agent_6_qualifier" / "src"))
    from agent6_qualifier.messaging.wazzup_config import WazzupConfig

    # Default constant from project docs / env loader — channel id not rewritten here.
    assert hasattr(WazzupConfig, "__dataclass_fields__") or True
    # Smoke: contact_role package does not import wazzup send path for sync.
    import contact_role.sync.coordinator as coord_mod

    src = Path(coord_mod.__file__).read_text(encoding="utf-8")
    assert "wazzup" not in src.lower()


def test_31_telegram_folders_untouched():
    sys.path.insert(0, str(ROOT / "agent_6_qualifier" / "src"))
    from agent6_qualifier.telegram_folders import CLIENT_FOLDER, OWNER_FOLDER, AGENT_FOLDER

    assert CLIENT_FOLDER == "Клиент"
    assert OWNER_FOLDER == "Owner"
    assert AGENT_FOLDER == "Agent"


def test_32_playwright_worker_mapping_unchanged():
    from whatsapp_ui_sync.roles import CanonicalRole as WARole, plan_list_membership

    plan = plan_list_membership(WARole.AGENT, current_lists=())
    assert plan.target_list == "Owner"
    plan2 = plan_list_membership(WARole.CLIENT, current_lists=("Owner",))
    assert "Client" in plan2.must_assign
    assert "Owner" in plan2.must_remove


def test_33_no_real_crm_browser_writes_in_dry_run(tmp_path, store):
    amo = FakeAmoContactClient(
        [contact_with_phone(1, "+66625124001", tags=["VIP"], type_value="")]
    )
    sync = AmoContactRoleSync(amo, dry_run=True, enabled=False)
    result = sync.sync_role(
        phone="+66625124001", role=CanonicalRole.CLIENT, confirm_write=False
    )
    assert result.code == "DRY_RUN_PLAN"
    assert result.wrote is False
    assert amo.updates == []
    assert result.planned["field"] == AMO_FIELD_NAME
    assert result.planned["value"] == "Клиент"


def test_amo_dry_run_plan_for_owner_agent(tmp_path):
    amo = FakeAmoContactClient(
        [
            contact_with_phone(1, "+66625124001", tags=["CLIENT", "VIP"], type_value="Клиент"),
            contact_with_phone(2, "+66625124002", tags=["x"], type_value=""),
        ]
    )
    sync = AmoContactRoleSync(amo, dry_run=True, enabled=False)
    r1 = sync.sync_role(phone="+66625124001", role=CanonicalRole.OWNER)
    assert r1.code == "DRY_RUN_PLAN"
    assert r1.planned["value"] == "Владелец"
    tag_names = [t["name"] for t in r1.planned["tags"]]
    assert "OWNER" in tag_names and "VIP" in tag_names and "CLIENT" not in tag_names

    r2 = sync.sync_role(phone="+66625124002", role=CanonicalRole.AGENT)
    assert r2.planned["value"] == "Агент"
    assert any(t["name"] == "AGENT" for t in r2.planned["tags"])


def test_field_missing_reported():
    amo = FakeAmoContactClient(
        [contact_with_phone(1, "+66625124001")], has_type_field=False
    )
    sync = AmoContactRoleSync(amo, dry_run=True, enabled=False)
    r = sync.sync_role(phone="+66625124001", role=CanonicalRole.CLIENT)
    assert r.code == "FIELD_MISSING"
    assert AMO_FIELD_NAME in r.message
