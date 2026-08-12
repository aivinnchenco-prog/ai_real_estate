"""Offline tests for dual-sync dry-run diagnose planner."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contact_role" / "src"))

from contact_role.diagnose import plan_dual_sync_dry_run
from contact_role.roles import CanonicalRole
from contact_role.sources import RoleSource
from contact_role.state import ContactRoleStore
from contact_role.sync.amocrm import FakeAmoContactClient, contact_with_phone


def test_dry_run_never_writes(tmp_path):
    store = ContactRoleStore(tmp_path / "roles.json")
    amo = FakeAmoContactClient(
        [contact_with_phone(1, "+66625124001", tags=["VIP"], type_value="")]
    )
    report = plan_dual_sync_dry_run(
        phone="+66625124001",
        role=CanonicalRole.CLIENT,
        source=RoleSource.MANUAL,
        store=store,
        amo=amo,
        wa_current_lists=["Owner"],
        wa_contact_found="YES",
    )
    assert report.amo_would_write == "NO"
    assert report.wa_would_write == "NO"
    assert report.amo_contact_resolved == "YES"
    assert report.amo_desired_field == "Клиент"
    assert report.wa_target_list == "Client"
    assert "Client" in report.wa_would_add
    assert "Owner" in report.wa_would_remove
    assert amo.updates == []
    assert store.get_by_phone("+66625124001") is None  # diagnose does not commit


def test_dry_run_priority_block(tmp_path):
    store = ContactRoleStore(tmp_path / "roles.json")
    from contact_role.assign import assign_known_role
    from contact_role.sync.amocrm import AmoContactRoleSync
    from contact_role.sync.coordinator import ContactRoleSyncCoordinator
    from contact_role.sync.outbox import ContactRoleSyncOutbox

    coord = ContactRoleSyncCoordinator(
        store=store,
        outbox=ContactRoleSyncOutbox(tmp_path / "jobs.json"),
        amo_sync=AmoContactRoleSync(FakeAmoContactClient([]), dry_run=True),
    )
    assign_known_role(
        phone="+66625124001",
        role=CanonicalRole.OWNER,
        source=RoleSource.MANUAL,
        store=store,
        coordinator=coord,
    )
    report = plan_dual_sync_dry_run(
        phone="+66625124001",
        role=CanonicalRole.CLIENT,
        source=RoleSource.MESSAGE_CLASSIFICATION,
        store=store,
        amo=None,
    )
    assert report.priority_allowed is False
    assert report.would_commit_canonical is False
    assert report.route == "AGENT_7"
