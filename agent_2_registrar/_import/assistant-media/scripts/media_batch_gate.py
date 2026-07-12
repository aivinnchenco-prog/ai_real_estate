#!/usr/bin/env python3
"""
Telegram photo batch gate — ждёт 150 сек после КАЖДОЙ новой пачки фото.
Photo count is synced from disk (source of truth).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / "data" / "sessions"
WAIT_SEC = 150
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _load_config() -> dict:
    p = ROOT / "config" / "pipeline.json"
    if p.exists():
        with p.open(encoding="utf-8") as f:
            return json.load(f)
    return {"batch_wait_seconds": WAIT_SEC}


def session_dir(session_id: str) -> Path:
    d = SESSIONS / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "photos").mkdir(exist_ok=True)
    return d


def state_path(session_id: str) -> Path:
    return session_dir(session_id) / "gate.json"


def count_photos_on_disk(session_id: str) -> int:
    photos_dir = session_dir(session_id) / "photos"
    if not photos_dir.exists():
        return 0
    return sum(1 for p in photos_dir.iterdir() if p.suffix.lower() in PHOTO_EXTS)


def load_state(session_id: str) -> dict:
    p = state_path(session_id)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_state(session_id: str, state: dict) -> None:
    with state_path(session_id).open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cmd_init(args: argparse.Namespace) -> int:
    d = session_dir(args.session)
    state = {
        "session_id": args.session,
        "created_at": now_iso(),
        "description_at": None,
        "last_batch_at": None,
        "batch_count": 0,
        "photo_count": 0,
        "photo_count_disk": 0,
        "ready": False,
    }
    if args.description:
        desc_path = d / "description.txt"
        desc_path.write_text(args.description, encoding="utf-8")
        state["description_at"] = now_iso()
    save_state(args.session, state)
    print(json.dumps({"status": "initialized", "session": args.session}))
    return 0


def cmd_bump(args: argparse.Namespace) -> int:
    state = load_state(args.session)
    if not state:
        print("Session not initialized. Run: init --session ...", file=sys.stderr)
        return 1
    on_disk = count_photos_on_disk(args.session)
    reported = args.count if args.count is not None else on_disk
    if args.count is not None and on_disk < args.count:
        print(
            json.dumps({"warning": "disk_count_less_than_reported", "disk": on_disk, "reported": reported}),
            file=sys.stderr,
        )
    state["batch_count"] = state.get("batch_count", 0) + 1
    state["photo_count"] = on_disk
    state["photo_count_disk"] = on_disk
    state["last_batch_at"] = now_iso()
    state["ready"] = False
    save_state(args.session, state)
    print(json.dumps({
        "status": "batch_recorded",
        "batch": state["batch_count"],
        "photos": on_disk,
        "wait_seconds": _load_config().get("batch_wait_seconds", WAIT_SEC),
    }))
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    cfg = _load_config()
    wait = cfg.get("batch_wait_seconds", WAIT_SEC)
    state = load_state(args.session)
    if not state:
        print("not_ready: no session")
        return 1
    if not state.get("description_at"):
        desc = session_dir(args.session) / "description.txt"
        if desc.exists():
            state["description_at"] = now_iso()
            save_state(args.session, state)
        else:
            print("not_ready: no description")
            return 1
    on_disk = count_photos_on_disk(args.session)
    state["photo_count"] = on_disk
    state["photo_count_disk"] = on_disk
    save_state(args.session, state)
    if on_disk < 1:
        print("not_ready: no photos on disk")
        return 1
    last = state.get("last_batch_at")
    if not last:
        print("not_ready: no batches")
        return 1
    last_ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
    elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
    if elapsed < wait:
        remaining = int(wait - elapsed)
        print(f"not_ready: wait {remaining}s more (batch idle < {wait}s)")
        return 1
    state["ready"] = True
    state["ready_at"] = now_iso()
    save_state(args.session, state)
    print("ready")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state(args.session)
    state["photo_count_disk"] = count_photos_on_disk(args.session)
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--session", required=True)
    p_init.add_argument("--description", default="")

    p_bump = sub.add_parser("bump")
    p_bump.add_argument("--session", required=True)
    p_bump.add_argument("--count", type=int, default=None)

    p_ready = sub.add_parser("ready")
    p_ready.add_argument("--session", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("--session", required=True)

    args = parser.parse_args()
    handlers = {"init": cmd_init, "bump": cmd_bump, "ready": cmd_ready, "status": cmd_status}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
