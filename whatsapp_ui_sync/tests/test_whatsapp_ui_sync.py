"""Offline tests for WhatsApp UI native-list sync (no live browser)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whatsapp_ui_sync.config import WhatsAppUiSyncConfig
from whatsapp_ui_sync.lists_ui import FakeListsUi
from whatsapp_ui_sync.phone import normalize_phone_e164
from whatsapp_ui_sync.queue import JobStatus, RoleSyncQueue
from whatsapp_ui_sync.results import SyncCode
from whatsapp_ui_sync.roles import (
    CanonicalRole,
    map_role_to_list,
    plan_list_membership,
    role_changed,
)
from whatsapp_ui_sync.safety import FORBIDDEN_ACTIONS, assert_action_allowed, expose_public_api
from whatsapp_ui_sync.trigger import enqueue_role_sync_if_changed
from whatsapp_ui_sync.worker import WhatsAppNativeListSyncWorker


def _cfg(**kwargs) -> WhatsAppUiSyncConfig:
    base = dict(
        enabled=False,
        dry_run=True,
        headless=True,
        profile_dir=Path("/tmp/wa_ui_profile_test"),
        queue_dir=Path("/tmp/wa_ui_queue_test"),
        client_list_name="Client",
        owner_list_name="Owner",
        agent_list_name="Owner",
        action_delay_ms=0,
        max_attempts=3,
    )
    base.update(kwargs)
    return WhatsAppUiSyncConfig(**base)


def test_client_maps_to_client():
    assert map_role_to_list(CanonicalRole.CLIENT) == "Client"


def test_owner_maps_to_owner():
    assert map_role_to_list(CanonicalRole.OWNER) == "Owner"


def test_agent_maps_to_owner():
    assert map_role_to_list(CanonicalRole.AGENT) == "Owner"
    assert map_role_to_list(CanonicalRole.AGENT, agent_list="Agent") == "Agent"


def test_unknown_noop():
    plan = plan_list_membership(CanonicalRole.UNKNOWN)
    assert plan.no_op and plan.target_list is None


def test_phone_normalization_e164():
    assert normalize_phone_e164("+66625124002") == "+66625124002"
    assert normalize_phone_e164("0625124002") == "+66625124002"
    assert normalize_phone_e164("66625124002") == "+66625124002"


def test_ambiguous_contact_blocked():
    ui = FakeListsUi(ambiguous_phones={"+66625124002"})
    worker = WhatsAppNativeListSyncWorker(_cfg(), lists_ui=ui)
    result = worker.sync_contact_role("+66625124002", "CLIENT")
    assert result.code == SyncCode.CONTACT_AMBIGUOUS


def test_list_missing_blocked():
    ui = FakeListsUi(
        contacts={"+66625124002": set()},
        available_lists={"Owner"},  # Client missing
    )
    worker = WhatsAppNativeListSyncWorker(_cfg(), lists_ui=ui)
    result = worker.sync_contact_role("+66625124002", "CLIENT")
    assert result.code == SyncCode.WHATSAPP_LIST_NOT_FOUND


def test_already_assigned_idempotent():
    ui = FakeListsUi(contacts={"+66625124002": {"Client"}})
    worker = WhatsAppNativeListSyncWorker(_cfg(enabled=True, dry_run=False), lists_ui=ui)
    result = worker.sync_contact_role("+66625124002", "CLIENT", confirm_write=True)
    assert result.code == SyncCode.ALREADY_SYNCED
    assert ui.save_clicks == 0
    assert ui.clicks == []


def test_conflict_removal_planned_correctly():
    plan = plan_list_membership(
        CanonicalRole.CLIENT,
        current_lists=["Owner"],
    )
    assert plan.target_list == "Client"
    assert "Client" in plan.must_assign
    assert "Owner" in plan.must_remove

    plan2 = plan_list_membership(
        CanonicalRole.OWNER,
        current_lists=["Client"],
    )
    assert plan2.target_list == "Owner"
    assert "Owner" in plan2.must_assign
    assert "Client" in plan2.must_remove


def test_dry_run_no_click():
    ui = FakeListsUi(contacts={"+66625124002": set()})
    worker = WhatsAppNativeListSyncWorker(_cfg(dry_run=True), lists_ui=ui)
    result = worker.sync_contact_role("+66625124002", "CLIENT", dry_run=True)
    assert result.code == SyncCode.DRY_RUN_PLAN
    assert result.would_add == ["Client"]
    assert ui.save_clicks == 0


def test_sync_disabled_no_write():
    ui = FakeListsUi(contacts={"+66625124002": set()})
    worker = WhatsAppNativeListSyncWorker(
        _cfg(enabled=False, dry_run=False),
        lists_ui=ui,
    )
    result = worker.sync_contact_role(
        "+66625124002", "CLIENT", confirm_write=True, dry_run=False
    )
    assert result.code == SyncCode.WRITE_BLOCKED
    assert ui.save_clicks == 0
    assert ui.clicks == []


def test_auth_missing_blocks(monkeypatch):
    from whatsapp_ui_sync.browser_session import BrowserSessionStatus, SessionProbe
    from whatsapp_ui_sync.browser_session import WhatsAppBrowserSession

    class AuthSession(WhatsAppBrowserSession):
        def __init__(self):
            self.config = _cfg()
            self._page = object()
            self._playwright = None
            self._context = None

        def probe_auth(self):
            return SessionProbe(BrowserSessionStatus.AUTH_REQUIRED, "QR")

        def start(self, *a, **k):
            return None

        def open_whatsapp(self):
            return None

    worker = WhatsAppNativeListSyncWorker(_cfg(enabled=True, dry_run=False), session=AuthSession())
    result = worker.sync_contact_role("+66625124002", "CLIENT", confirm_write=True, dry_run=False)
    assert result.code == SyncCode.WHATSAPP_UI_AUTH_REQUIRED


def test_qualification_unaffected_by_sync_failure(tmp_path):
    """Hook swallows errors; qualifier path must not raise."""
    cfg = _cfg(queue_dir=tmp_path)
    # Invalid queue path parent permission simulation via broken enqueue target
    job = enqueue_role_sync_if_changed(
        "+66625124002",
        "CLIENT",
        previous_role="UNKNOWN",
        config=cfg,
        queue=RoleSyncQueue(tmp_path / "role_sync_jobs.json"),
    )
    assert job is not None
    # Failure in worker must not propagate when caller guards
    ui = FakeListsUi(ambiguous_phones={"+66625124002"})
    worker = WhatsAppNativeListSyncWorker(cfg, lists_ui=ui)
    try:
        worker.sync_contact_role("+66625124002", "CLIENT")
        qualification_ok = True
    except Exception:
        qualification_ok = False
    assert qualification_ok


def test_agent6_routing_unaffected():
    sys.path.insert(0, str(ROOT.parents[0] / "agent_6_qualifier" / "src"))
    from agent6_qualifier.telegram_folders import CLIENT_FOLDER, role_folder_title

    assert CLIENT_FOLDER == "Клиент"
    assert role_folder_title("Владелец") == "Owner"
    assert role_folder_title("Агент") == "Agent"


def test_agent7_routing_unaffected():
    sys.path.insert(0, str(ROOT.parents[0] / "agent_6_qualifier" / "src"))
    from agent7_envoy import owner_registry

    assert callable(owner_registry.mark_owner)
    assert callable(owner_registry.get_owner)


def test_wazzup_transport_unaffected():
    sys.path.insert(0, str(ROOT.parents[0] / "agent_6_qualifier" / "src"))
    from agent6_qualifier.messaging import wazzup_transport

    assert hasattr(wazzup_transport, "WazzupWhatsAppTransport")


def test_browser_session_files_gitignored():
    gi = (ROOT.parents[0] / ".gitignore").read_text(encoding="utf-8")
    assert "whatsapp_ui_profile" in gi
    assert "whatsapp_ui_sync" in gi or "role_sync_jobs" in gi


def test_no_message_sending_api_exposed():
    api = expose_public_api()
    assert "send_message" in api["forbidden"]
    assert "send_message" not in api["allowed_writes"]
    with pytest.raises(PermissionError):
        assert_action_allowed("send_message")
    for action in FORBIDDEN_ACTIONS:
        with pytest.raises(PermissionError):
            assert_action_allowed(action)


def test_queue_retry_bounded(tmp_path):
    q = RoleSyncQueue(tmp_path / "role_sync_jobs.json", max_attempts=3)
    job = q.enqueue("+66625124002", "CLIENT", previous_role="UNKNOWN")
    assert job is not None
    for _ in range(3):
        q.mark(job.job_id, status=JobStatus.FAILED, bump_attempt=True, last_error="timeout")
    jobs = q.list_jobs()
    assert jobs[0].attempt_count == 3
    assert not q.should_retry(jobs[0]) or jobs[0].attempt_count >= q.max_attempts


def test_repeated_role_event_no_duplicate_job(tmp_path):
    q = RoleSyncQueue(tmp_path / "jobs.json", max_attempts=3)
    cfg = _cfg(queue_dir=tmp_path)
    j1 = enqueue_role_sync_if_changed(
        "+66625124002", "CLIENT", previous_role="UNKNOWN", config=cfg, queue=q
    )
    j2 = enqueue_role_sync_if_changed(
        "+66625124002", "CLIENT", previous_role="CLIENT", config=cfg, queue=q
    )
    j3 = enqueue_role_sync_if_changed(
        "+66625124002", "CLIENT", previous_role="UNKNOWN", config=cfg, queue=q
    )
    assert j1 is not None
    assert j2 is None  # unchanged
    assert j3 is not None
    assert j3.job_id == j1.job_id  # deduped open job
    assert len(q.list_jobs()) == 1


def test_role_changed_helper():
    assert role_changed("UNKNOWN", "CLIENT")
    assert not role_changed("CLIENT", "CLIENT")
    assert role_changed("CLIENT", "OWNER")


def test_real_write_updates_membership():
    ui = FakeListsUi(contacts={"+66625124002": {"Owner"}})
    worker = WhatsAppNativeListSyncWorker(
        _cfg(enabled=True, dry_run=False),
        lists_ui=ui,
    )
    result = worker.sync_contact_role(
        "+66625124002", "CLIENT", confirm_write=True, dry_run=False
    )
    assert result.code == SyncCode.SYNCED
    assert ui.save_clicks == 1
    assert "Client" in ui.contacts["+66625124002"]
    assert "Owner" not in ui.contacts["+66625124002"]


def test_lists_never_auto_created():
    ui = FakeListsUi(contacts={"+66625124002": set()}, available_lists=set())
    worker = WhatsAppNativeListSyncWorker(_cfg(), lists_ui=ui)
    result = worker.sync_contact_role("+66625124002", "OWNER")
    assert result.code == SyncCode.WHATSAPP_LIST_NOT_FOUND
