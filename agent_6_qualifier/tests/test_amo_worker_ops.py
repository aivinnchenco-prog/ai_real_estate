"""Operational tests for amoCRM SLA worker wiring and staging diagnostics."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.amo_tasks_diagnose import format_report, run_diagnosis
from agent6_qualifier.amo_task_service import AmoTaskService
from agent6_qualifier.amo_task_state import mark_escalated, was_escalated
from agent6_qualifier.amo_tasks_config import format_task_text
from agent6_qualifier.amo_worker_config import validate_worker_startup, worker_startup_ok


DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"


def _load_worker_module():
    worker_path = Path(__file__).resolve().parents[1] / "scripts" / "amo_task_worker.py"
    spec = importlib.util.spec_from_file_location("amo_task_worker", worker_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWorkerStartupConfig:
    def test_missing_token_when_enabled(self, monkeypatch):
        monkeypatch.setenv("AMO_TASKS_ENABLED", "true")
        monkeypatch.delenv("AMO_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("AMO_SUBDOMAIN", "test")
        errors = validate_worker_startup()
        assert any("AMO_ACCESS_TOKEN" in e for e in errors)
        assert not worker_startup_ok()

    def test_valid_config_ok(self, monkeypatch):
        monkeypatch.setenv("AMO_TASKS_ENABLED", "true")
        monkeypatch.setenv("AMO_ACCESS_TOKEN", "token")
        monkeypatch.setenv("AMO_SUBDOMAIN", "test")
        monkeypatch.delenv("AMO_TASK_TYPE_ID", raising=False)
        assert validate_worker_startup() == []
        assert worker_startup_ok()

    def test_invalid_task_type_id(self, monkeypatch):
        monkeypatch.setenv("AMO_TASKS_ENABLED", "true")
        monkeypatch.setenv("AMO_ACCESS_TOKEN", "token")
        monkeypatch.setenv("AMO_SUBDOMAIN", "test")
        monkeypatch.setenv("AMO_TASK_TYPE_ID", "bad")
        errors = validate_worker_startup()
        assert any("AMO_TASK_TYPE_ID" in e for e in errors)

    def test_disabled_skips_validation(self, monkeypatch):
        monkeypatch.setenv("AMO_TASKS_ENABLED", "false")
        monkeypatch.delenv("AMO_ACCESS_TOKEN", raising=False)
        assert validate_worker_startup() == []


class TestWorkerOneShot:
    def test_run_once_exits_on_config_error(self, monkeypatch, capsys):
        w = _load_worker_module()
        monkeypatch.setattr(w, "amo_tasks_enabled", lambda: True)
        monkeypatch.setattr(w, "validate_worker_startup", lambda: ["AMO_SUBDOMAIN not set"])
        code = w.run_once()
        captured = capsys.readouterr().out
        assert code == 1
        assert "config error" in captured
        assert "AMO_ACCESS_TOKEN" not in captured or "config error" in captured

    def test_run_once_processes_overdue(self, monkeypatch):
        w = _load_worker_module()
        monkeypatch.setattr(w, "amo_tasks_enabled", lambda: True)
        monkeypatch.setattr(w, "validate_worker_startup", lambda: [])
        monkeypatch.setenv("AMO_SUBDOMAIN", "test")
        monkeypatch.setenv("AMO_ACCESS_TOKEN", "x")
        past = int(time.time()) - 60
        tasks = [
            {"id": 1, "entity_id": 10, "text": format_task_text("owner_followup", "x"),
             "complete_till": past},
        ]
        monkeypatch.setattr(w, "list_open_openhome_tasks", lambda amo: tasks)
        processed = []
        monkeypatch.setattr(
            AmoTaskService, "process_overdue_task",
            lambda self, task, **k: processed.append(task["id"]),
        )
        assert w.run_once() == 0
        assert processed == [1]

    def test_repeated_run_idempotent_escalation(self, monkeypatch, tmp_path):
        import agent6_qualifier.amo_task_state as st
        monkeypatch.setattr(st, "_STATE_PATH", tmp_path / "state.json")
        amo = MagicMock()
        svc = AmoTaskService(amo)
        task = {"id": 9, "entity_id": 42, "text": format_task_text("owner_followup", "x")}
        monkeypatch.setattr(svc, "ensure_task", MagicMock(return_value=99))
        monkeypatch.setattr("agent6_qualifier.amo_task_service.notify_manager", MagicMock())
        svc._escalate_owner_overdue(42, task)
        svc._escalate_owner_overdue(42, task)
        assert was_escalated(42, "owner_followup")
        assert svc.ensure_task.call_count == 1


class TestDeployWiring:
    def test_service_invokes_run_script(self):
        service = (DEPLOY_DIR / "amo-task-worker.service").read_text(encoding="utf-8")
        assert "Type=oneshot" in service
        assert "run_amo_task_worker.sh" in service
        assert "amo_task_worker.py" not in service

    def test_timer_restart_safe(self):
        timer = (DEPLOY_DIR / "amo-task-worker.timer").read_text(encoding="utf-8")
        assert "Persistent=true" in timer
        assert "OnBootSec=2min" in timer
        assert "OnUnitActiveSec=10min" in timer
        assert "amo-task-worker.service" in timer

    def test_install_enables_timer(self):
        script = (DEPLOY_DIR / "install_amo_task_worker.sh").read_text(encoding="utf-8")
        assert "systemctl enable --now amo-task-worker.timer" in script
        assert "daemon-reload" in script


class TestDiagnose:
    def test_read_only_endpoints_only(self, monkeypatch):
        calls: list[tuple[str, str]] = []

        class FakeAmo:
            def get_account(self, *, with_params: str = ""):
                calls.append(("GET", f"/account?with={with_params}"))
                return {
                    "id": 1,
                    "current_user_id": 10,
                    "_embedded": {"task_types": [{"id": 1, "name": "Follow-up"}]},
                }

            def list_users(self, **kwargs):
                calls.append(("GET", "/users"))
                return [{"id": 123}, {"id": 456}]

        monkeypatch.setenv("AMO_DEFAULT_RESPONSIBLE_USER_ID", "123")
        monkeypatch.setenv("AMO_MANAGER_RESPONSIBLE_USER_ID", "456")
        report = run_diagnosis(FakeAmo())
        text = format_report(report)
        assert "amo connection: OK" in text
        assert "task_type_id 1: VALID" in text
        assert "default responsible user: VALID" in text
        assert "manager responsible user: VALID" in text
        assert all(method == "GET" for method, _ in calls)

    def test_invalid_responsible_user(self, monkeypatch):
        class FakeAmo:
            def get_account(self, *, with_params: str = ""):
                return {
                    "id": 1,
                    "current_user_id": 10,
                    "_embedded": {"task_types": [{"id": 1}]},
                }

            def list_users(self, **kwargs):
                return [{"id": 100}]

        monkeypatch.setenv("AMO_DEFAULT_RESPONSIBLE_USER_ID", "999")
        report = run_diagnosis(FakeAmo())
        assert not report.ok
        assert any("default responsible user" in line.label and line.status == "FAIL"
                   for line in report.lines)

    def test_diagnose_script_redacts_token(self, monkeypatch, capsys, tmp_path):
        diag_path = Path(__file__).resolve().parents[1] / "scripts" / "amo_tasks_diagnose.py"
        spec = importlib.util.spec_from_file_location("amo_tasks_diagnose_cli", diag_path)
        cli = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "amo_tasks_diagnose_cli", cli)
        spec.loader.exec_module(cli)

        secret = "eyJ.super.secret.token"
        monkeypatch.setenv("AMO_ACCESS_TOKEN", secret)
        monkeypatch.setenv("AMO_SUBDOMAIN", "test")
        monkeypatch.setattr(cli, "AmoClient", lambda: (_ for _ in ()).throw(RuntimeError(secret)))

        code = cli.main()
        out = capsys.readouterr().out
        assert secret not in out
        assert code == 1
