#!/usr/bin/env python3
"""Serve local Wazzup webhook (shared Agent 6 core).

Requires in .env:
  WAZZUP_WEBHOOK_ENABLED=true

Outbound policy (independent kill switches):
  WAZZUP_SEND_ENABLED=false          → no POST
  WAZZUP_AUTO_REPLY_ENABLED=false    → process business, suppress bot POST
  WAZZUP_LIVE_ALLOWLIST_ENABLED=true → live POST only for listed phones
  WAZZUP_STAGE_MODE=true             → staging: requires allowlist; one live
                                       reply per webhook batch

Production-like live (any new WhatsApp client):
  WAZZUP_STAGE_MODE=false
  WAZZUP_LIVE_ALLOWLIST_ENABLED=false
  WAZZUP_LIVE_ALLOWLIST_PHONES=

Usage:
  cd agent_6_qualifier
  WAZZUP_WEBHOOK_ENABLED=true PYTHONPATH=src python3 scripts/wazzup_webhook_serve.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


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
    _load_dotenv(_ROOT / ".env")
    host = os.getenv("WAZZUP_WEBHOOK_HOST", "127.0.0.1")
    port = int(os.getenv("WAZZUP_WEBHOOK_PORT", "8765"))

    from agent6_qualifier.messaging.wazzup_config import load_wazzup_config
    from agent6_qualifier.messaging.webhook_http import serve_wazzup_webhook

    cfg = load_wazzup_config()
    print("=== Wazzup webhook serve (shared Agent6 core) ===")
    print(f"webhook_enabled={cfg.webhook_enabled}")
    print(f"send_enabled={cfg.send_enabled}")
    print(f"auto_reply_enabled={cfg.auto_reply_enabled}")
    print(f"live_allowlist_enabled={cfg.live_allowlist_enabled}")
    print(f"allowlist_count={len(cfg.live_allowlist_phones)}")
    print(f"stage_mode={cfg.stage_mode}")
    print(f"channel_id={cfg.channel_id}")
    if not cfg.webhook_enabled:
        print("Set WAZZUP_WEBHOOK_ENABLED=true in .env to serve inbound")
        return 3
    # Align with outbound_guard: STAGE_MODE requires allowlist.
    # Production-like (STAGE_MODE=false) may run without allowlist.
    if (
        cfg.send_enabled
        and cfg.auto_reply_enabled
        and cfg.stage_mode
        and not cfg.live_allowlist_enabled
    ):
        print(
            "REFUSE: WAZZUP_STAGE_MODE=true requires "
            "WAZZUP_LIVE_ALLOWLIST_ENABLED=true + phones "
            "(or set WAZZUP_STAGE_MODE=false for production-like live)"
        )
        return 4
    if (
        cfg.send_enabled
        and cfg.auto_reply_enabled
        and not cfg.live_allowlist_enabled
        and not cfg.stage_mode
    ):
        print(
            "NOTICE: production-like live — allowlist OFF, stage_mode OFF "
            "(any inbound WhatsApp client may receive Agent6 replies)"
        )
    serve_wazzup_webhook(
        host=host,
        port=port,
        store_path=_ROOT / "data" / "wazzup_processed.sqlite",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
