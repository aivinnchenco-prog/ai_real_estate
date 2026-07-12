#!/usr/bin/env python3
"""Append-only event log per object_id."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "data" / "events"


def log_event(object_id: str, step: str, event: str, **extra: Any) -> None:
    EVENTS.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "object_id": object_id,
        "step": step,
        "event": event,
        **extra,
    }
    path = EVENTS / f"{object_id}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
