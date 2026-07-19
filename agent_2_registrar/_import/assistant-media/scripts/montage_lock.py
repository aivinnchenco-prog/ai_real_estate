#!/usr/bin/env python3
"""Один активный монтаж (Agent 3 / fal) на весь сервер — файловый lock."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parents[1] / "data" / "montage.lock"


class MontageBusyError(RuntimeError):
    def __init__(self, holder: str = ""):
        self.holder = holder.strip()
        msg = "montage already running"
        if self.holder:
            msg += f" ({self.holder})"
        super().__init__(msg)


def _read_holder(fd: int) -> str:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, 256).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


@contextmanager
def montage_file_lock(object_id: str, *, blocking: bool = False):
    """Эксклюзивный lock: второй процесс (бот / chain-watcher) сразу отказывается."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError:
            raise MontageBusyError(_read_holder(fd)) from None
        os.ftruncate(fd, 0)
        os.write(fd, object_id.encode("utf-8"))
        os.fsync(fd)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
