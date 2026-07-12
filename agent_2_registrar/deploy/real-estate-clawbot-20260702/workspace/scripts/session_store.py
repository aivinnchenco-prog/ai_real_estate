#!/usr/bin/env python3
"""One object_id per session — persisted in session.json."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / "data" / "sessions"


def session_dir(session_id: str) -> Path:
    d = SESSIONS / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "photos").mkdir(exist_ok=True)
    return d


def session_json_path(session_id: str) -> Path:
    return session_dir(session_id) / "session.json"


def load_session(session_id: str) -> dict[str, Any]:
    p = session_json_path(session_id)
    if not p.exists():
        return {"session_id": session_id}
    with p.open(encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("session_id", session_id)
    return data


def save_session(session_id: str, data: dict[str, Any]) -> dict[str, Any]:
    data = {**load_session(session_id), **data, "session_id": session_id}
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    if "created_at" not in data:
        data["created_at"] = data["updated_at"]
    with session_json_path(session_id).open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def get_object_id(session_id: str) -> str | None:
    return load_session(session_id).get("object_id")


def bind_object_id(session_id: str, object_id: str) -> dict[str, Any]:
    return save_session(session_id, {"object_id": object_id})
