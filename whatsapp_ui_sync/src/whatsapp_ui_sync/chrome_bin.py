"""Locate Google Chrome / Chromium binaries (macOS + Linux VPS)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def chrome_candidates() -> list[Path]:
    env = (os.getenv("WHATSAPP_UI_CHROME_BIN") or "").strip()
    out: list[Path] = []
    if env:
        out.append(Path(env).expanduser())
    out.extend(
        [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium-browser"),
            Path("/usr/bin/chromium"),
            Path("/snap/bin/chromium"),
        ]
    )
    which = shutil.which("google-chrome-stable") or shutil.which("google-chrome")
    if which:
        out.append(Path(which))
    which_c = shutil.which("chromium-browser") or shutil.which("chromium")
    if which_c:
        out.append(Path(which_c))
    # de-dupe
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def find_chrome_binary() -> Path | None:
    for path in chrome_candidates():
        try:
            if path.is_file() and os.access(path, os.X_OK):
                return path
        except Exception:
            continue
    return None


def default_native_profile_dir() -> Path:
    override = (os.getenv("WHATSAPP_UI_PROFILE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openhome" / "whatsapp_ui_chrome_native"
