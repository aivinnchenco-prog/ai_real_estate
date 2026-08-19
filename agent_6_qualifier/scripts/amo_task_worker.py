#!/usr/bin/env python3
"""Idempotent amoCRM SLA watcher — overdue OPENHOME tasks.

Run periodically (cron/systemd timer), not inside message handlers.
Restart-safe: uses amo task state + escalation markers on disk.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_dotenv() -> None:
    """Python parser only — never bash-source production .env."""
    candidates = [
        Path(os.getenv("OPENHOME_ENV_FILE") or "/opt/openhome/.env"),
        ROOT / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, _, value = text.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


_load_dotenv()

from agent6_qualifier.amo import AmoClient  # noqa: E402
from agent6_qualifier.amo_task_service import AmoTaskService  # noqa: E402
from agent6_qualifier.amo_tasks_config import amo_tasks_enabled, parse_task_key, task_prefix  # noqa: E402
from agent6_qualifier.amo_worker_config import validate_worker_startup  # noqa: E402


def list_open_openhome_tasks(amo: AmoClient, *, page_limit: int = 50) -> list[dict]:
    """Open tasks with our prefix (scan recent open lead tasks)."""
    prefix = task_prefix()
    marker = f"[{prefix}:"
    found: list[dict] = []
    page = 1
    while page <= 10:
        batch = amo.list_tasks(is_completed=False, page=page, limit=page_limit)
        if not batch:
            break
        for task in batch:
            text = task.get("text") or ""
            if marker in text and parse_task_key(text):
                found.append(task)
        if len(batch) < page_limit:
            break
        page += 1
    return found


def run_once() -> int:
    if not amo_tasks_enabled():
        print("amo tasks disabled")
        return 0
    errors = validate_worker_startup()
    if errors:
        for err in errors:
            print(f"amo_task_worker config error: {err}")
        return 1
    amo = AmoClient()
    service = AmoTaskService(amo)
    now = int(time.time())
    processed = 0
    for task in list_open_openhome_tasks(amo):
        complete_till = int(task.get("complete_till") or 0)
        if complete_till and complete_till > now:
            continue
        service.process_overdue_task(task)
        processed += 1
    print(f"amo_task_worker: processed {processed} overdue tasks")
    return 0


def main() -> int:
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
