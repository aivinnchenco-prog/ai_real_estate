#!/usr/bin/env python3
"""Validate amo chat channel credentials already placed in .env (no CLI secrets)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[0]
sys.path.insert(0, str(ROOT / "src"))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    _load_dotenv(REPO / ".env")
    _load_dotenv(ROOT / ".env")
    _load_dotenv(Path(os.getenv("OPENHOME_ENV_FILE") or "/opt/openhome/.env"))

    from agent7_envoy.amo_chat.channel_config import (
        format_channel_presence_report,
        validate_channel_config,
    )

    print(format_channel_presence_report(), end="")
    data = validate_channel_config()
    ok = all(p.credentials_ready for p in data.values())
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
