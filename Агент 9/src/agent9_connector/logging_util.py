"""Safe structured logging for Agent 9."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import data_dir

SENSITIVE_KEYS = ("token", "api_key", "authorization", "cookie", "password", "secret")


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if any(s in k.lower() for s in SENSITIVE_KEYS) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        token = os.getenv("AGENT9_GEMINI_API_KEY", "")
        if token and token in value:
            return value.replace(token, "***")
        notion = os.getenv("AGENT9_NOTION_TOKEN", "")
        if notion and notion in value:
            return value.replace(notion, "***")
    return value


def log_event(event: str, **fields: Any) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **_redact(fields),
    }
    path = log_dir() / "connector.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
