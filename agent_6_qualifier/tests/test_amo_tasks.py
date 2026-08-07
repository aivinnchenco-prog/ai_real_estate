"""Offline tests for amoCRM task lifecycle (not asyncio.create_task)."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.amo import AmoClient
from agent6_qualifier.amo_next_action import resolve_next_action
from agent6_qualifier.amo_task_service import AmoTaskService
from agent6_qualifier.amo_task_state import (
    client_auto_followups_sent,
    increment_client_auto_followups,
    mark_escalated,
    was_escalated,
)
from agent6_qualifier.amo_tasks_config import format_task_text, parse_task_key
from agent6_qualifier.qualifier import Session


@pytest.fixture
def amo(monkeypatch):
    monkeypatch.setenv("AMO_SUBDOMAIN", "test")
    monkeypatch.setenv("AMO_ACCESS_TOKEN", "token")
    monkeypatch.setenv("AMO_DEFAULT_RESPONSIBLE_USER_ID", "100")
    client = AmoClient()
    client.pipeline_id = 1
    return client


def make_req(responses):
    def _req(method, path, **kw):
        for prefix, resp in responses.items():
            if path.startswith(prefix):
                return resp() if callable(resp) else resp
        raise AssertionError(f"unexpected {method} {path}")
    return _req


class TestAmoClientTasks:
    def test_create_task_payload_leads(self, amo, monkeypatch):
        captured = {}
        def _req(method, path, **kw):
            if method == "POST" and path == "/tasks":
                captured.update(kw.get("json", [{}])[0])
                return {"_embedded": {"tasks": [{"id": 501}]}}
            raise AssertionError(path)
        monkeypatch.setattr(amo, "_req", _req)
        tid = amo.create_task(
            lead_id=77, text="[OPENHOME:owner_followup]\nbody",
            complete_till=int(time.time()) + 3600,
            responsible_user_id=100, task_type_id=1,
        )
        assert tid == 501
        assert captured["entity_type"] == "leads"
        assert captured["entity_id"] == 77

    def test_list_tasks_open(self, amo, monkeypatch):
        monkeypatch.setattr(amo, "_req", make_req({
            "/tasks": {"_embedded": {"tasks": [{"id": 1, "is_completed": False}]}},
        }))
        tasks = amo.list_tasks(entity_id=77, is_completed=False)
        assert len(tasks) == 1

    def test_complete_task(self, amo, monkeypatch):
        calls = []
        def _req(method, path, **kw):
            calls.append((method, path, kw))
            return {}
        monkeypatch.setattr(amo, "_req", _req)
        amo.complete_task(99, "done")
        assert calls[0][0] == "PATCH"
        assert calls[0][2]["json"]["is_completed"] is True

    def test_api_error_handled_by_service(self, amo, monkeypatch):
        monkeypatch.setattr(amo, "_req", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        errors = []
        svc = AmoTaskService(amo, notify_error_fn=lambda c, e, ctx: errors.append((c, e)))
        assert svc.ensure_task(1, "need_human", "x", deadline_ts=int(time.time()) + 60) is None
        assert errors


class TestEnsureTask:
    def test_creates_when_missing(self, amo, monkeypatch):
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [])
        monkeypatch.setattr(amo, "get_lead", lambda lid: {"responsible_user_id": 100})
        monkeypatch.setattr(amo, "create_task", lambda **k: 42)
        svc = AmoTaskService(amo)
        tid = svc.ensure_task(1, "owner_followup", "body", deadline_ts=999)
        assert tid == 42

    def test_returns_existing_open(self, amo, monkeypatch):
        existing = {
            "id": 7,
            "text": format_task_text("owner_followup", "old"),
            "complete_till": 100,
        }
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [existing])
        monkeypatch.setattr(amo, "update_task", MagicMock())
        svc = AmoTaskService(amo)
        tid = svc.ensure_task(1, "owner_followup", "new body", deadline_ts=200)
        assert tid == 7

    def test_duplicate_event_no_duplicate_create(self, amo, monkeypatch):
        create = MagicMock(return_value=1)
        existing = {"id": 3, "text": format_task_text("owner_followup", "x"), "complete_till": 1}
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [existing])
        monkeypatch.setattr(amo, "create_task", create)
        svc = AmoTaskService(amo)
        svc.ensure_task(1, "owner_followup", "x", deadline_ts=2)
        svc.ensure_task(1, "owner_followup", "x", deadline_ts=2)
        create.assert_not_called()


class TestOwnerSLA:
    def _session(self):
        s = Session(chat_id="1", amo_lead_id=10)
        s.lead.preferred_object_id = "A_001"
        from datetime import date
        s.lead.check_in = date(2026, 9, 1)
        s.lead.check_out = date(2026, 9, 30)
        return s

    def test_outreach_creates_owner_followup(self, amo, monkeypatch):
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [])
        monkeypatch.setattr(amo, "get_lead", lambda lid: {"responsible_user_id": 100})
        monkeypatch.setattr(amo, "create_task", lambda **k: 55)
        svc = AmoTaskService(amo)
        tid = svc.on_owner_outreach_sent(self._session())
        assert tid == 55

    def test_owner_reply_completes_task(self, amo, monkeypatch):
        task = {"id": 8, "text": format_task_text("owner_followup", "x")}
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [task])
        complete = MagicMock()
        monkeypatch.setattr(amo, "complete_task", complete)
        svc = AmoTaskService(amo)
        svc.on_owner_response(self._session(), "free")
        complete.assert_called_once()

    def test_unknown_owner_message_does_not_complete(self, amo, monkeypatch):
        complete = MagicMock()
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [])
        monkeypatch.setattr(amo, "complete_task", complete)
        svc = AmoTaskService(amo)
        svc.on_owner_response(self._session(), "unknown")
        complete.assert_not_called()

    def test_reconcile_closes_owner_on_conditions_stage(self, amo, monkeypatch):
        task = {"id": 1, "text": format_task_text("owner_followup", "x")}
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [task])
        complete = MagicMock()
        monkeypatch.setattr(amo, "complete_task", complete)
        AmoTaskService(amo).reconcile_stage(10, "Согласование условий")
        complete.assert_called()


class TestClientSLA:
    def test_awaiting_response_creates_followup(self, amo, monkeypatch):
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [])
        monkeypatch.setattr(amo, "get_lead", lambda lid: {"responsible_user_id": 100})
        monkeypatch.setattr(amo, "create_task", lambda **k: 61)
        s = Session(chat_id="2", amo_lead_id=20)
        tid = AmoTaskService(amo).on_client_outbound_awaiting_response(s, "Когда заезд?")
        assert tid == 61

    def test_inbound_completes_followup(self, amo, monkeypatch):
        task = {"id": 2, "text": format_task_text("client_followup", "x")}
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [task])
        complete = MagicMock()
        monkeypatch.setattr(amo, "complete_task", complete)
        AmoTaskService(amo).on_client_inbound(Session(chat_id="2", amo_lead_id=20))
        complete.assert_called_once()


class TestNeedHuman:
    def test_handoff_creates_task_and_telegram(self, amo, monkeypatch):
        monkeypatch.setattr(amo, "list_tasks", lambda **k: [])
        monkeypatch.setenv("AMO_MANAGER_RESPONSIBLE_USER_ID", "200")
        monkeypatch.setattr(amo, "create_task", lambda **k: 88)
        mgr = MagicMock(return_value=True)
        s = Session(chat_id="3", amo_lead_id=30)
        s.lead.preferred_object_id = "A_X"
        AmoTaskService(amo).on_need_human(s, "help", notify_fn=mgr)
        mgr.assert_called_once()
        assert mgr.call_args.kwargs.get("dedup_key") == "3"

    def test_crm_failure_does_not_block_telegram(self, amo, monkeypatch):
        monkeypatch.setattr(amo, "list_tasks", lambda **k: (_ for _ in ()).throw(RuntimeError("amo")))
        mgr = MagicMock(return_value=True)
        s = Session(chat_id="3", amo_lead_id=30)
        AmoTaskService(amo, notify_error_fn=lambda *a: None).on_need_human(s, "help", notify_fn=mgr)
        mgr.assert_called_once()


class TestNextAction:
    def test_need_human_priority(self):
        tasks = [{"text": format_task_text("client_followup", "x")},
                 {"text": format_task_text("need_human", "x")}]
        assert resolve_next_action("Квалификация", tasks) == "need_human"

    def test_stage_owner_request(self):
        assert resolve_next_action("Запрос владельцу", []) == "owner_followup"


class TestEscalationState:
    def test_escalation_dedup(self, monkeypatch, tmp_path):
        state_file = tmp_path / "state.json"
        import agent6_qualifier.amo_task_state as st
        monkeypatch.setattr(st, "_STATE_PATH", state_file)
        assert not was_escalated(1, "owner_followup")
        mark_escalated(1, "owner_followup")
        assert was_escalated(1, "owner_followup")

    def test_client_followup_counter(self, monkeypatch, tmp_path):
        import agent6_qualifier.amo_task_state as st
        monkeypatch.setattr(st, "_STATE_PATH", tmp_path / "s.json")
        assert client_auto_followups_sent(5) == 0
        assert increment_client_auto_followups(5) == 1


class TestTaskKeyParsing:
    def test_parse_key(self):
        text = format_task_text("owner_followup", "Проверить ответ")
        assert parse_task_key(text) == "owner_followup"

    def test_non_openhome_ignored(self):
        assert parse_task_key("random task") is None


class TestWorker:
    def test_worker_processes_only_overdue(self, amo, monkeypatch):
        import importlib.util
        worker_path = Path(__file__).resolve().parents[1] / "scripts" / "amo_task_worker.py"
        spec = importlib.util.spec_from_file_location("amo_task_worker", worker_path)
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        past = int(time.time()) - 100
        future = int(time.time()) + 10000
        tasks = [
            {"id": 1, "entity_id": 10, "text": format_task_text("owner_followup", "x"),
             "complete_till": past},
            {"id": 2, "entity_id": 11, "text": format_task_text("client_followup", "x"),
             "complete_till": future},
        ]
        monkeypatch.setattr(w, "list_open_openhome_tasks", lambda a: tasks)
        processed = []
        monkeypatch.setattr(
            AmoTaskService, "process_overdue_task",
            lambda self, task, **k: processed.append(task["id"]),
        )
        monkeypatch.setattr(w, "amo_tasks_enabled", lambda: True)
        monkeypatch.setenv("AMO_ACCESS_TOKEN", "x")
        w.run_once()
        assert processed == [1]
