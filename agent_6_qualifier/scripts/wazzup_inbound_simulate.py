#!/usr/bin/env python3
"""Simulate one real-shaped Wazzup inbound → Agent 6 qualification dry-run.

No network POST to Wazzup. Does not require public tunnel.

Usage:
  cd agent_6_qualifier
  PYTHONPATH=src python3 scripts/wazzup_inbound_simulate.py
"""

from __future__ import annotations

import json
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


REAL_SHAPED_V3 = {
    "_fixture": "synthetic/real-shaped v3 messages[]",
    "messages": [
        {
            "messageId": "sim-inbound-001",
            "channelId": "7ac4092a-cf3c-450f-8518-d7cc7bd3f995",
            "chatId": "66999999002",
            "chatType": "whatsapp",
            "direction": "inbound",
            "text": "Ищу виллу на Пхукете на месяц",
            "dateTime": "2026-08-09T13:00:00Z",
            "status": "inbound",
            "contact": {"name": "Test Client", "phone": "66999999002"},
        }
    ],
}


def main() -> int:
    _load_dotenv(_ROOT / ".env")
    # Force inbound processing for local simulate; never enable send.
    os.environ["WAZZUP_WEBHOOK_ENABLED"] = "true"
    os.environ["WAZZUP_SEND_ENABLED"] = "false"
    os.environ["WAZZUP_AUTO_REPLY_ENABLED"] = "false"

    from agent6_qualifier.messaging.idempotency import ProcessedEventStore
    from agent6_qualifier.messaging.wazzup_config import load_wazzup_config
    from agent6_qualifier.messaging.webhook import process_wazzup_webhook

    cfg = load_wazzup_config()
    assert cfg.send_enabled is False
    assert cfg.auto_reply_enabled is False

    store = ProcessedEventStore(_ROOT / "data" / "wazzup_sim_processed.sqlite")
    result = process_wazzup_webhook(
        REAL_SHAPED_V3,
        config=cfg,
        store=store,
        run_qualification_dry_run=True,
        amo=None,
    )
    print(json.dumps({
        "accepted": result.accepted,
        "messages": len(result.messages),
        "duplicates": result.duplicates,
        "auto_reply": result.auto_reply,
        "send_enabled": cfg.send_enabled,
    }, ensure_ascii=False, indent=2))
    for dry in result.dry_runs:
        print("\n".join(dry.to_log_lines()))
    if not result.dry_runs and result.messages:
        print("NOTE: messages accepted but dry-run empty (outbound?)")
    return 0 if result.dry_runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
