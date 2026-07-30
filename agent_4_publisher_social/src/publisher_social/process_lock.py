from __future__ import annotations

import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from .dotenv_util import package_root


class PublisherLockBusy(RuntimeError):
    def __init__(self, path: Path, owner: dict[str, Any] | None = None):
        self.path = path
        self.owner = owner or {}
        details = []
        if self.owner.get("pid"):
            details.append(f"pid={self.owner['pid']}")
        if self.owner.get("command"):
            details.append(f"command={self.owner['command']}")
        suffix = f" ({', '.join(details)})" if details else ""
        super().__init__(f"publisher lock is busy: {path}{suffix}")


class PublisherProcessLock:
    """Crash-safe, cross-process lock for phone and shared state operations."""

    def __init__(
        self,
        command: str,
        *,
        path: Path | None = None,
        timeout: float = 0.0,
        poll_interval: float = 0.1,
    ):
        self.command = command
        self.path = path or (package_root() / "data" / "publisher.lock")
        self.timeout = max(0.0, timeout)
        self.poll_interval = max(0.01, poll_interval)
        self._file: IO[str] | None = None

    def _read_owner(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            return json.loads(raw) if raw else {}
        except (OSError, ValueError):
            return {}

    def __enter__(self) -> PublisherProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    owner = self._read_owner()
                    lock_file.close()
                    raise PublisherLockBusy(self.path, owner)
                time.sleep(self.poll_interval)

        self._file = lock_file
        owner = {
            "pid": os.getpid(),
            "command": self.command,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        lock_file.seek(0)
        lock_file.truncate()
        json.dump(owner, lock_file, ensure_ascii=False)
        lock_file.write("\n")
        lock_file.flush()
        os.fsync(lock_file.fileno())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._file is None:
            return
        try:
            self._file.seek(0)
            self._file.truncate()
            self._file.flush()
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
