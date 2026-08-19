#!/usr/bin/env python3
"""Durable PostMyPost publication_id mapping per listing slot (JSON file)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STATE_VERSION = 1


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def state_path() -> Path:
    override = (os.getenv("PUBLISHER_PMP_STATE_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return package_root() / "data" / "postmypost_publications.json"


def postmypost_slot_id(
    platform: str,
    *,
    post_kind: str | None = None,
    upload_video: bool = False,
) -> str:
    """Canonical slot key: instagram:carousel, tiktok:video, x:post, …"""
    from publish_pipeline import instagram_post_kind, network_for

    network = network_for(platform)
    if network == "instagram":
        kind = post_kind or instagram_post_kind(upload_video=upload_video, mode=None)
        return f"instagram:{kind}"
    if network == "tiktok":
        # Как и колонка post_url_*: карусель только по явному признаку.
        if post_kind == "carousel" and not upload_video:
            return "tiktok:carousel"
        return "tiktok:video"
    if network == "youtube":
        return f"{network}:video"
    return f"{network}:post"


def _empty_state() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "slots": {}}


def load_state(path: Path | None = None) -> dict[str, Any]:
    path = path or state_path()
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    data.setdefault("version", _STATE_VERSION)
    data.setdefault("slots", {})
    return data


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def slot_key(page_id: str, slot_id: str) -> str:
    return f"{page_id}:{slot_id}"


def register_publication(
    *,
    page_id: str,
    object_id: str,
    platform: str,
    publication_id: str,
    planner_url: str,
    scheduled_time: str,
    post_kind: str | None = None,
    upload_video: bool = False,
    path: Path | None = None,
) -> dict[str, Any]:
    state = load_state(path)
    sid = postmypost_slot_id(platform, post_kind=post_kind, upload_video=upload_video)
    key = slot_key(page_id, sid)
    entry = {
        "page_id": page_id,
        "object_id": object_id,
        "platform": platform,
        "slot_id": sid,
        "publication_id": str(publication_id),
        "planner_url": planner_url,
        "scheduled_time": scheduled_time,
        "post_kind": post_kind,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    state["slots"][key] = entry
    save_state(state, path)
    return entry


def lookup_publication(
    page_id: str,
    platform: str,
    *,
    post_kind: str | None = None,
    upload_video: bool = False,
    path: Path | None = None,
) -> dict[str, Any] | None:
    state = load_state(path)
    sid = postmypost_slot_id(platform, post_kind=post_kind, upload_video=upload_video)
    return state.get("slots", {}).get(slot_key(page_id, sid))


def lookup_by_publication_id(
    publication_id: str,
    path: Path | None = None,
) -> dict[str, Any] | None:
    state = load_state(path)
    for entry in state.get("slots", {}).values():
        if str(entry.get("publication_id")) == str(publication_id):
            return entry
    return None
