"""Read-only amoCRM staging diagnostics for operational tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .amo import AmoClient
from .amo_tasks_config import (
    default_responsible_user_id,
    default_task_type_id,
    manager_responsible_user_id,
)


@dataclass
class DiagnosisLine:
    label: str
    status: str  # OK | FAIL | SKIP | WARN
    detail: str = ""


@dataclass
class DiagnosisReport:
    lines: list[DiagnosisLine] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(line.status in ("OK", "SKIP", "WARN") for line in self.lines)

    def add(self, label: str, status: str, detail: str = "") -> None:
        self.lines.append(DiagnosisLine(label=label, status=status, detail=detail))


def _user_ids(users: list[dict]) -> set[int]:
    ids: set[int] = set()
    for user in users:
        uid = user.get("id")
        if uid is not None:
            ids.add(int(uid))
    return ids


def _task_type_ids(account: dict) -> set[int]:
    types = (account.get("_embedded") or {}).get("task_types") or []
    ids: set[int] = set()
    for item in types:
        tid = item.get("id")
        if tid is not None:
            ids.add(int(tid))
    return ids


def run_diagnosis(amo: AmoClient) -> DiagnosisReport:
    """GET-only checks against staging amoCRM."""
    report = DiagnosisReport()
    methods_used: list[str] = []

    try:
        account = amo.get_account(with_params="task_types")
        methods_used.append("GET /api/v4/account?with=task_types")
    except Exception as exc:
        report.add("amo connection", "FAIL", str(exc))
        return report

    report.add("amo connection", "OK", f"account id={account.get('id', '?')}")
    current_user = account.get("current_user_id")
    if current_user:
        report.add("current user context", "OK", f"user_id={current_user}")
    else:
        report.add("current user context", "WARN", "current_user_id missing in account")

    users: list[dict] = []
    try:
        users = amo.list_users()
        methods_used.append("GET /api/v4/users")
        report.add("users list", "OK", f"count={len(users)}")
    except Exception as exc:
        report.add("users list", "FAIL", str(exc))

    task_ids = _task_type_ids(account)
    configured_type = default_task_type_id()
    if task_ids:
        if configured_type in task_ids:
            report.add(
                f"task_type_id {configured_type}",
                "VALID",
                "present in account task_types",
            )
        else:
            report.add(
                f"task_type_id {configured_type}",
                "FAIL",
                f"not in account types: {sorted(task_ids)}",
            )
    else:
        report.add(
            f"task_type_id {configured_type}",
            "WARN",
            "account returned no task_types; cannot verify remotely",
        )

    user_id_set = _user_ids(users) if users else set()

    def _check_user(label: str, uid: int | None) -> None:
        if uid is None:
            report.add(label, "SKIP", "not configured")
            return
        if not user_id_set:
            report.add(label, "WARN", f"id {uid} configured but users list unavailable")
            return
        if uid in user_id_set:
            report.add(label, "VALID", f"user {uid}")
        else:
            report.add(label, "FAIL", f"user {uid} not found")

    _check_user("default responsible user", default_responsible_user_id())
    _check_user("manager responsible user", manager_responsible_user_id())

    report.add("read-only endpoints", "OK", ", ".join(methods_used))
    return report


def format_report(report: DiagnosisReport) -> str:
    lines: list[str] = []
    for item in report.lines:
        text = f"{item.label}: {item.status}"
        if item.detail:
            text = f"{text} ({item.detail})"
        lines.append(text)
    return "\n".join(lines)


def assert_read_only_methods(methods: list[tuple[str, str]]) -> None:
    """Guard for tests: diagnostic must not mutate amoCRM."""
    for method, _path in methods:
        if method.upper() not in ("GET", "HEAD"):
            raise AssertionError(f"non read-only amo call: {method}")
