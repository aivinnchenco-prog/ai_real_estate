from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import state_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_state() -> dict[str, Any]:
    return {"objects": {}, "daily": {}, "scheduled": {}, "notion_outbox": {}}


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"state must be a JSON object: {path}")
    raw.setdefault("objects", {})
    raw.setdefault("daily", {})
    raw.setdefault("scheduled", {})
    raw.setdefault("notion_outbox", {})
    return raw


@contextlib.contextmanager
def _state_write_lock(path: Path) -> Iterator[None]:
    """Serialize state mutations even when helpers are called outside the CLI lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write_state(path: Path, state: dict[str, Any]) -> None:
    """Write in the same directory and replace atomically after fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Some Android filesystems do not support fsync on directories.
            pass
    finally:
        if temp_path.exists():
            temp_path.unlink()


def load_state() -> dict[str, Any]:
    return _read_state(state_path())


def save_state(state: dict[str, Any]) -> None:
    path = state_path()
    with _state_write_lock(path):
        _atomic_write_state(path, state)


def _mutate_state(
    state: dict[str, Any],
    mutator,
) -> Any:
    """
    Reload under an exclusive lock, apply one mutation and refresh the caller's
    snapshot. This avoids lost updates between independent CLI processes.
    """
    path = state_path()
    with _state_write_lock(path):
        current = _read_state(path)
        result = mutator(current)
        _atomic_write_state(path, current)
    state.clear()
    state.update(current)
    return result


def object_entry(state: dict[str, Any], object_id: str) -> dict[str, Any]:
    objects = state.setdefault("objects", {})
    return objects.setdefault(object_id, {"channels_done": {}, "log": []})


def object_has_progress(state: dict[str, Any], object_id: str) -> bool:
    """У объекта уже есть хотя бы один успешный канал — продолжаем без паузы."""
    entry = object_entry(state, object_id)
    if entry.get("channels_done"):
        return True
    return bool(entry.get("fb_groups_published"))


def normalize_fb_group_url(url: str) -> str:
    return url.strip().rstrip("/") + "/"


def fb_groups_published_map(state: dict[str, Any], object_id: str) -> dict[str, Any]:
    entry = object_entry(state, object_id)
    raw = entry.get("fb_groups_published") or {}
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def is_fb_group_published(state: dict[str, Any], object_id: str, group_url: str) -> bool:
    key = normalize_fb_group_url(group_url)
    return key in fb_groups_published_map(state, object_id)


def mark_fb_group_published(
    state: dict[str, Any],
    object_id: str,
    group_url: str,
    *,
    note: str | None = None,
) -> None:
    key = normalize_fb_group_url(group_url)
    now = _now_iso()

    def mutate(current: dict[str, Any]) -> None:
        entry = object_entry(current, object_id)
        published = entry.setdefault("fb_groups_published", {})
        published[key] = {"at": now, "note": note}
        if note:
            entry.setdefault("log", []).append(f"{now} fb_groups {key}: {note}")

    _mutate_state(state, mutate)


def clear_fb_groups_published(state: dict[str, Any], object_id: str) -> None:
    def mutate(current: dict[str, Any]) -> None:
        object_entry(current, object_id).pop("fb_groups_published", None)

    _mutate_state(state, mutate)


def pending_fb_group_urls(
    state: dict[str, Any],
    object_id: str,
    group_urls: list[str],
) -> list[str]:
    return [url for url in group_urls if not is_fb_group_published(state, object_id, url)]


def all_fb_groups_published(
    state: dict[str, Any],
    object_id: str,
    group_urls: list[str],
) -> bool:
    if not group_urls:
        return False
    return not pending_fb_group_urls(state, object_id, group_urls)


def is_channel_done(state: dict[str, Any], object_id: str, channel: str) -> bool:
    entry = object_entry(state, object_id)
    done = entry.get("channels_done") or {}
    return bool(done.get(channel))


def mark_channel_done(
    state: dict[str, Any],
    object_id: str,
    channel: str,
    *,
    post_url: str | None = None,
    note: str | None = None,
    status: str = "submitted_unverified",
    daily_date: str | None = None,
) -> bool:
    """Mark once and optionally increment the channel's daily counter atomically."""
    now = _now_iso()

    def mutate(current: dict[str, Any]) -> bool:
        entry = object_entry(current, object_id)
        done = entry.setdefault("channels_done", {})
        if done.get(channel):
            entry.setdefault("channels_inflight", {}).pop(channel, None)
            return False
        done[channel] = {
            "at": now,
            "status": status,
            "post_url": post_url,
            "note": note,
        }
        entry.setdefault("channels_inflight", {}).pop(channel, None)
        if note:
            entry.setdefault("log", []).append(f"{now} {channel}: {note}")
        if daily_date:
            day = current.setdefault("daily", {}).setdefault(daily_date, {})
            day[channel] = int(day.get(channel) or 0) + 1
        return True

    return bool(_mutate_state(state, mutate))


def mark_channel_inflight(
    state: dict[str, Any],
    object_id: str,
    channel: str,
) -> None:
    now = _now_iso()

    def mutate(current: dict[str, Any]) -> None:
        entry = object_entry(current, object_id)
        entry.setdefault("channels_inflight", {})[channel] = {"started_at": now}

    _mutate_state(state, mutate)


def clear_channel_inflight(
    state: dict[str, Any],
    object_id: str,
    channel: str,
) -> None:
    def mutate(current: dict[str, Any]) -> None:
        object_entry(current, object_id).setdefault("channels_inflight", {}).pop(
            channel, None
        )

    _mutate_state(state, mutate)


def mark_channel_verified(
    state: dict[str, Any],
    object_id: str,
    channel: str,
    *,
    post_url: str,
) -> None:
    now = _now_iso()

    def mutate(current: dict[str, Any]) -> None:
        entry = object_entry(current, object_id)
        done = entry.setdefault("channels_done", {})
        channel_state = done.setdefault(channel, {"at": now, "note": None})
        channel_state["status"] = "verified"
        channel_state["post_url"] = post_url
        channel_state["verified_at"] = now
        entry.setdefault("channels_inflight", {}).pop(channel, None)

    _mutate_state(state, mutate)


def invalidate_channel_post_url(
    state: dict[str, Any],
    object_id: str,
    channel: str,
    *,
    note: str,
) -> None:
    """Снять ошибочную верификацию, сохранив защиту от повторной публикации."""
    now = _now_iso()

    def mutate(current: dict[str, Any]) -> None:
        entry = object_entry(current, object_id)
        done = entry.setdefault("channels_done", {})
        channel_state = done.setdefault(channel, {"at": now})
        channel_state["status"] = "submitted_unverified"
        channel_state["post_url"] = None
        channel_state["note"] = note
        channel_state["invalidated_at"] = now
        channel_state.pop("verified_at", None)
        entry.setdefault("channels_inflight", {}).pop(channel, None)
        entry.setdefault("log", []).append(f"{now} {channel}: {note}")

    _mutate_state(state, mutate)


def channel_inflight(
    state: dict[str, Any],
    object_id: str,
    channel: str,
) -> dict[str, Any] | None:
    entry = object_entry(state, object_id)
    return (entry.get("channels_inflight") or {}).get(channel)


def append_log(state: dict[str, Any], object_id: str, line: str) -> None:
    stamped = f"{_now_iso()} {line}"

    def mutate(current: dict[str, Any]) -> None:
        object_entry(current, object_id).setdefault("log", []).append(stamped)

    _mutate_state(state, mutate)


def save_job_snapshot(job_dict: dict[str, Any], jobs_dir: Path) -> Path:
    jobs_dir.mkdir(parents=True, exist_ok=True)
    object_id = job_dict.get("object_id") or "unknown"
    path = jobs_dir / f"{object_id}.json"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(job_dict, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return path


def clear_channel_done(
    state: dict[str, Any],
    object_id: str,
    channel: str,
) -> None:
    def mutate(current: dict[str, Any]) -> None:
        entry = object_entry(current, object_id)
        entry.setdefault("channels_done", {}).pop(channel, None)
        entry.setdefault("channels_inflight", {}).pop(channel, None)

    _mutate_state(state, mutate)


def schedule_channel(
    state: dict[str, Any],
    *,
    object_id: str,
    page_id: str,
    channel: str,
    publish_after: str,
    after_channel: str | None = None,
) -> None:
    key = f"{object_id}:{channel}"
    created_at = _now_iso()

    def mutate(current: dict[str, Any]) -> None:
        scheduled = current.setdefault("scheduled", {})
        scheduled[key] = {
            "object_id": object_id,
            "page_id": page_id,
            "channel": channel,
            "publish_after": publish_after,
            "after_channel": after_channel,
            "created_at": created_at,
        }

    _mutate_state(state, mutate)


def due_scheduled(state: dict[str, Any], *, now_iso: str | None = None) -> list[dict[str, Any]]:
    now = now_iso or _now_iso()
    scheduled = state.get("scheduled") or {}
    due: list[dict[str, Any]] = []
    for entry in scheduled.values():
        if entry.get("publish_after", "") <= now:
            due.append(entry)
    due.sort(key=lambda e: e.get("publish_after", ""))
    return due


def clear_scheduled(
    state: dict[str, Any],
    *,
    object_id: str,
    channel: str,
) -> None:
    key = f"{object_id}:{channel}"

    def mutate(current: dict[str, Any]) -> None:
        current.setdefault("scheduled", {}).pop(key, None)

    _mutate_state(state, mutate)


def daily_count(state: dict[str, Any], day: str, channel: str) -> int:
    return int(((state.get("daily") or {}).get(day) or {}).get(channel) or 0)


def enqueue_notion_update(
    state: dict[str, Any],
    *,
    key: str,
    page_id: str,
    object_id: str,
    channel: str,
    properties: dict[str, Any],
) -> None:
    now = _now_iso()

    def mutate(current: dict[str, Any]) -> None:
        existing = current.setdefault("notion_outbox", {}).get(key) or {}
        current["notion_outbox"][key] = {
            "key": key,
            "page_id": page_id,
            "object_id": object_id,
            "channel": channel,
            "properties": properties,
            "created_at": existing.get("created_at") or now,
            "attempts": int(existing.get("attempts") or 0),
            "last_error": existing.get("last_error"),
        }

    _mutate_state(state, mutate)


def pending_notion_updates(state: dict[str, Any]) -> list[dict[str, Any]]:
    entries = list((state.get("notion_outbox") or {}).values())
    entries.sort(key=lambda entry: entry.get("created_at", ""))
    return entries


def mark_notion_update_failed(
    state: dict[str, Any],
    key: str,
    error: str,
) -> None:
    def mutate(current: dict[str, Any]) -> None:
        entry = current.setdefault("notion_outbox", {}).get(key)
        if not entry:
            return
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["last_error"] = error[:1000]
        entry["last_attempt_at"] = _now_iso()

    _mutate_state(state, mutate)


def clear_notion_update(state: dict[str, Any], key: str) -> None:
    def mutate(current: dict[str, Any]) -> None:
        current.setdefault("notion_outbox", {}).pop(key, None)

    _mutate_state(state, mutate)
