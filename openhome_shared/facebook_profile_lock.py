"""Cross-process lock for Chromium persistent Facebook profiles.

Playwright ``launch_persistent_context(user_data_dir=...)`` must not run twice
on the same directory. Use one flock file per profile directory (derived from
the profile folder name unless OPENHOME_FB_PROFILE_LOCK is set).
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path


def resolve_profile_lock_path(profile_dir: Path | None = None) -> Path:
    explicit = os.getenv("OPENHOME_FB_PROFILE_LOCK", "").strip()
    if explicit:
        return Path(explicit)
    if profile_dir is None:
        profile_dir = _agent1_profile_dir_from_env()
    name = profile_dir.name or "facebook_profile"
    lock_dir = os.getenv("OPENHOME_FB_LOCK_DIR", "").strip()
    if not lock_dir:
        default_lock = Path("/opt/openhome/runtime/state/shared/locks")
        try:
            default_lock.mkdir(parents=True, exist_ok=True)
            lock_dir = str(default_lock)
        except OSError:
            lock_dir = str(Path.cwd() / "data" / "locks")
    return Path(lock_dir) / f"{name}.lock"


def _agent1_profile_dir_from_env() -> Path:
    """Fallback when lock path is resolved without an explicit profile (Agent 1)."""
    raw = os.getenv("FB_BROWSER_PROFILE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path("/opt/openhome/runtime/browser_profiles/facebook_owner_outreach")


@contextlib.contextmanager
def facebook_profile_lock(
    profile_dir: Path | None = None,
    *,
    timeout_sec: float = 180.0,
):
    path = resolve_profile_lock_path(profile_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    deadline = time.time() + timeout_sec
    acquired = False
    try:
        while time.time() < deadline:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                time.sleep(0.5)
        if not acquired:
            raise TimeoutError(f"Facebook profile lock timeout ({path})")
        yield
    finally:
        if acquired:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)
