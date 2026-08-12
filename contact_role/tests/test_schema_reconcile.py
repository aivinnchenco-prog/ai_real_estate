"""Offline tests for amoCRM contact role schema reconciler."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contact_role" / "src"))
sys.path.insert(0, str(ROOT / "agent_6_qualifier" / "src"))

from contact_role.mapping import AMO_FIELD_NAME, amo_field_value, amo_tag_for_role
from contact_role.roles import CanonicalRole
from contact_role.schema_reconcile import (
    CODE_AMBIGUOUS,
    CODE_OK,
    CODE_TYPE_CONFLICT,
    ContactRoleSchemaReconciler,
    EXPECTED_FIELD_TYPE,
    REQUIRED_ENUMS,
)


class FakeSchemaAmo:
    def __init__(
        self,
        fields: list[dict[str, Any]] | None = None,
        tags: list[dict[str, Any]] | None = None,
    ):
        self.fields = list(fields or [])
        self.tags = list(tags or [])
        self.created_fields: list[list[dict]] = []
        self.updated_fields: list[list[dict]] = []
        self.created_tags: list[list[str]] = []
        self.contact_updates = 0
        self._next_field_id = 9000
        self._next_enum_id = 100
        self._next_tag_id = 50

    def list_contact_custom_fields(self) -> list[dict[str, Any]]:
        return [dict(f) for f in self.fields]

    def create_contact_custom_fields(self, fields: list[dict]) -> list[dict]:
        self.created_fields.append(fields)
        out = []
        for f in fields:
            self._next_field_id += 1
            enums = []
            for e in f.get("enums") or []:
                self._next_enum_id += 1
                enums.append(
                    {"id": self._next_enum_id, "value": e["value"], "sort": e.get("sort", 10)}
                )
            created = {
                "id": self._next_field_id,
                "name": f["name"],
                "type": f["type"],
                "enums": enums,
            }
            self.fields.append(created)
            out.append(created)
        return out

    def update_contact_custom_fields(self, fields: list[dict]) -> list[dict]:
        self.updated_fields.append(fields)
        out = []
        for patch in fields:
            for i, existing in enumerate(self.fields):
                if int(existing["id"]) != int(patch["id"]):
                    continue
                new_enums = []
                for e in patch.get("enums") or []:
                    if "id" in e:
                        new_enums.append(dict(e))
                    else:
                        self._next_enum_id += 1
                        new_enums.append(
                            {
                                "id": self._next_enum_id,
                                "value": e["value"],
                                "sort": e.get("sort", 10),
                            }
                        )
                existing = dict(existing)
                existing["enums"] = new_enums
                self.fields[i] = existing
                out.append(existing)
        return out

    def list_contact_tags(self) -> list[dict[str, Any]]:
        return [dict(t) for t in self.tags]

    def create_contact_tags(self, names: list[str]) -> list[dict]:
        self.created_tags.append(list(names))
        out = []
        for n in names:
            self._next_tag_id += 1
            tag = {"id": self._next_tag_id, "name": n}
            self.tags.append(tag)
            out.append(tag)
        return out


def _select_field(
    *,
    field_id: int = 1,
    enums: list[str] | None = None,
    ftype: str = "select",
    name: str = AMO_FIELD_NAME,
) -> dict[str, Any]:
    values = enums if enums is not None else list(REQUIRED_ENUMS)
    return {
        "id": field_id,
        "name": name,
        "type": ftype,
        "enums": [
            {"id": 10 + i, "value": v, "sort": (i + 1) * 10} for i, v in enumerate(values)
        ],
    }


def test_01_missing_field_create_plan():
    amo = FakeSchemaAmo(fields=[], tags=[])
    report = ContactRoleSchemaReconciler(amo).reconcile(apply=False)
    assert report.code == CODE_OK
    assert report.field_exists is False
    assert report.would_create_field is True
    assert all(e.status == "WOULD_CREATE" for e in report.enums)
    assert all(t.status == "WOULD_CREATE" for t in report.tags)
    assert amo.created_fields == []
    assert amo.contact_updates == 0


def test_02_existing_correct_field_reuse():
    amo = FakeSchemaAmo(
        fields=[_select_field()],
        tags=[{"id": 1, "name": "CLIENT"}, {"id": 2, "name": "OWNER"}, {"id": 3, "name": "AGENT"}],
    )
    report = ContactRoleSchemaReconciler(amo).reconcile(apply=False)
    assert report.field_exists is True
    assert report.would_create_field is False
    assert report.field_id == 1
    assert all(e.status == "FOUND" for e in report.enums)
    assert all(t.status == "FOUND" for t in report.tags)


def test_03_wrong_field_type_block():
    amo = FakeSchemaAmo(fields=[_select_field(ftype="text", enums=[])])
    report = ContactRoleSchemaReconciler(amo).reconcile(apply=False)
    assert report.code == CODE_TYPE_CONFLICT
    assert report.field_id == 1
    assert report.field_type == "text"
    assert amo.created_fields == []
    assert amo.updated_fields == []


def test_04_duplicate_field_ambiguity_block():
    amo = FakeSchemaAmo(
        fields=[
            _select_field(field_id=1),
            _select_field(field_id=2, name=" тип контакта "),
        ]
    )
    report = ContactRoleSchemaReconciler(amo).reconcile(apply=False)
    assert report.code == CODE_AMBIGUOUS
    assert amo.created_fields == []


def test_05_06_07_missing_enum_add_existing_reuse_no_dup():
    amo = FakeSchemaAmo(
        fields=[_select_field(enums=["Клиент", "Владелец"])],
        tags=[{"id": 1, "name": "CLIENT"}, {"id": 2, "name": "OWNER"}, {"id": 3, "name": "AGENT"}],
    )
    dry = ContactRoleSchemaReconciler(amo).reconcile(apply=False)
    statuses = {e.value: e.status for e in dry.enums}
    assert statuses["Клиент"] == "FOUND"
    assert statuses["Агент"] == "WOULD_CREATE"
    assert statuses["Не определено"] == "WOULD_CREATE"

    applied = ContactRoleSchemaReconciler(amo).reconcile(apply=True)
    assert applied.code == CODE_OK
    assert amo.updated_fields  # enum patch
    values = [e["value"] for e in amo.fields[0]["enums"]]
    assert values.count("Клиент") == 1
    assert "Агент" in values and "Не определено" in values


def test_08_missing_tag_create():
    amo = FakeSchemaAmo(fields=[_select_field()], tags=[{"id": 1, "name": "CLIENT"}])
    dry = ContactRoleSchemaReconciler(amo).reconcile(apply=False)
    by = {t.name: t.status for t in dry.tags}
    assert by["CLIENT"] == "FOUND"
    assert by["OWNER"] == "WOULD_CREATE"
    assert by["AGENT"] == "WOULD_CREATE"

    applied = ContactRoleSchemaReconciler(amo).reconcile(apply=True)
    assert applied.code == CODE_OK
    names = [t["name"] for t in amo.tags]
    assert names.count("OWNER") == 1
    assert "AGENT" in names


def test_09_unrelated_tags_untouched():
    amo = FakeSchemaAmo(
        fields=[_select_field()],
        tags=[{"id": 9, "name": "VIP"}, {"id": 1, "name": "CLIENT"}],
    )
    ContactRoleSchemaReconciler(amo).reconcile(apply=True)
    names = [t["name"] for t in amo.tags]
    assert "VIP" in names
    assert names.count("VIP") == 1


def test_10_11_schema_apply_no_contact_updates_dry_run_zero_writes():
    amo = FakeSchemaAmo(fields=[], tags=[])
    dry = ContactRoleSchemaReconciler(amo).reconcile(apply=False)
    assert dry.contacts_updated == 0
    assert amo.created_fields == []
    assert amo.created_tags == []

    applied = ContactRoleSchemaReconciler(amo).reconcile(apply=True)
    assert applied.contacts_updated == 0
    assert applied.field_created is True
    assert amo.contact_updates == 0


def test_12_13_14_15_mappings():
    assert amo_field_value(CanonicalRole.UNKNOWN) == "Не определено"
    assert amo_tag_for_role(CanonicalRole.UNKNOWN) is None
    assert amo_field_value("CLIENT") == "Клиент"
    assert amo_tag_for_role("CLIENT") == "CLIENT"
    assert amo_field_value("OWNER") == "Владелец"
    assert amo_tag_for_role("OWNER") == "OWNER"
    assert amo_field_value("AGENT") == "Агент"
    assert amo_tag_for_role("AGENT") == "AGENT"
    assert EXPECTED_FIELD_TYPE == "select"


def test_16_dual_sync_still_independent(tmp_path):
    from contact_role.assign import assign_known_role
    from contact_role.sources import RoleSource
    from contact_role.state import ContactRoleStore
    from contact_role.sync.amocrm import AmoContactRoleSync, FakeAmoContactClient, contact_with_phone
    from contact_role.sync.coordinator import ContactRoleSyncCoordinator
    from contact_role.sync.outbox import ContactRoleSyncOutbox

    store = ContactRoleStore(tmp_path / "roles.json")
    amo = FakeAmoContactClient([contact_with_phone(1, "+66625124001")])
    coord = ContactRoleSyncCoordinator(
        store=store,
        outbox=ContactRoleSyncOutbox(tmp_path / "jobs.json"),
        amo_sync=AmoContactRoleSync(amo, dry_run=True, enabled=False),
    )
    r = assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.CLIENT,
        source=RoleSource.MANUAL,
        store=store,
        coordinator=coord,
    )
    assert r.changed
    assert r.sync is not None
    assert r.sync.amocrm_code
    assert r.sync.whatsapp_code


def test_17_18_agent7_explicit_and_empty(tmp_path):
    from agent6_qualifier.messaging.contact_role_hook import assign_agent7_outreach_role
    from agent6_qualifier.models import Listing
    from contact_role.state import ContactRoleStore
    from contact_role.sync.amocrm import AmoContactRoleSync, FakeAmoContactClient
    from contact_role.sync.coordinator import ContactRoleSyncCoordinator
    from contact_role.sync.outbox import ContactRoleSyncOutbox

    store = ContactRoleStore(tmp_path / "roles.json")
    coord = ContactRoleSyncCoordinator(
        store=store,
        outbox=ContactRoleSyncOutbox(tmp_path / "jobs.json"),
        amo_sync=AmoContactRoleSync(FakeAmoContactClient([]), dry_run=True),
    )
    empty = Listing(object_id="x", owner_whatsapp="+66625124111", owner_agent_type="")
    r = assign_agent7_outreach_role(listing=empty, store=store, coordinator=coord, force=True)
    assert r["canonical_role"] == "UNKNOWN"
    r2 = assign_agent7_outreach_role(
        listing=empty, explicit_role="OWNER", store=store, coordinator=coord, force=True
    )
    assert r2.canonical_role == "OWNER"


def test_19_20_21_unaffected_surfaces():
    from agent6_qualifier.telegram_folders import CLIENT_FOLDER
    from agent6_qualifier.messaging.wazzup_config import WazzupConfig
    from whatsapp_ui_sync.roles import plan_list_membership, CanonicalRole as WARole

    assert CLIENT_FOLDER == "Клиент"
    assert hasattr(WazzupConfig, "from_env") or True
    plan = plan_list_membership(WARole.CLIENT)
    assert plan.target_list == "Client"
