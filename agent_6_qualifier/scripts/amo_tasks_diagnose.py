#!/usr/bin/env python3
"""Read-only amoCRM staging diagnostics for OPENHOME task config.

Usage:
  python3 scripts/amo_tasks_diagnose.py

Does not create or modify leads, tasks, pipelines, or contacts.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_ENV_PATH = ROOT / ".env"
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

from agent6_qualifier.amo import AmoClient  # noqa: E402
from agent6_qualifier.amo_tasks_diagnose import format_report, run_diagnosis  # noqa: E402
from agent6_qualifier.amo_worker_config import validate_worker_startup  # noqa: E402


def _redact(text: str) -> str:
    token = (os.getenv("AMO_ACCESS_TOKEN") or "").strip()
    if token and token in text:
        return text.replace(token, "***REDACTED***")
    return text


def main() -> int:
    local_errors = validate_worker_startup()
    if local_errors:
        for err in local_errors:
            print(f"local config: FAIL ({err})")
        return 1

    try:
        amo = AmoClient()
        report = run_diagnosis(amo)
        print(format_report(report))
        if not report.ok:
            return 1
        return 0
    except Exception as exc:
        print(_redact(str(exc)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
