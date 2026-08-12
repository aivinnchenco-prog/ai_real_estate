#!/usr/bin/env python3
"""Read-only Wazzup diagnostic for Agent 6 WhatsApp transport.

GET only. Never prints WAZZUP_API_KEY.

Usage:
  cd agent_6_qualifier
  PYTHONPATH=src python3 scripts/wazzup_diagnose.py
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

    from agent6_qualifier.messaging.wazzup_client import WazzupClient, normalize_channel
    from agent6_qualifier.messaging.wazzup_config import load_wazzup_config
    from agent6_qualifier.messaging.wazzup_errors import WazzupError, WazzupTokenMissing

    cfg = load_wazzup_config()
    print("=== Agent 6 Wazzup diagnostic (GET only) ===")
    print(f"API key:              {'SET' if cfg.api_key_set else 'MISSING'}")
    print(f"WAZZUP_CHANNEL_ID:    {cfg.channel_id}")
    print(f"API base:             {cfg.api_base_url}")
    print(f"Send enabled:         {cfg.send_enabled}")
    print(f"Webhook enabled:      {cfg.webhook_enabled}")
    print(f"Auto reply enabled:   {cfg.auto_reply_enabled}")
    print(
        f"External AR confirmed OFF: {cfg.external_autoresponse_confirmed_off}"
    )
    print()

    if not cfg.api_key_set:
        print("Wazzup API: FAILED")
        print("WAZZUP_TOKEN_MISSING: set WAZZUP_API_KEY in local .env (never commit)")
        return 2

    client = WazzupClient(cfg)
    try:
        raw_channels = client.get_channels()
        normalized = [normalize_channel(c) for c in raw_channels]
        print("Wazzup API: CONNECTED")
        print(f"Channels visible: {len(normalized)}")
        expected = next(
            (c for c in normalized if c.get("channel_id") == cfg.channel_id),
            None,
        )
        if expected is None:
            print(f"Channel {cfg.channel_id}: NOT FOUND")
            return 3
        print()
        print("Expected channel:")
        print(f"  Channel ID: {expected.get('channel_id')}")
        print(f"  Transport:  {expected.get('transport')}")
        print(f"  State:      {expected.get('state')}")
        print(f"  Plain ID:   {expected.get('plain_id')}")

        ok_transport = (expected.get("transport") or "") == cfg.expected_transport
        ok_state = (expected.get("state") or "") == cfg.expected_state
        ok_plain = str(expected.get("plain_id") or "") == cfg.expected_plain_id
        print()
        print(
            f"CHECK transport: {'OK' if ok_transport else 'FAIL'} "
            f"(expected {cfg.expected_transport})"
        )
        print(
            f"CHECK state:     {'OK' if ok_state else 'FAIL'} "
            f"(expected {cfg.expected_state})"
        )
        print(
            f"CHECK plainId:   {'OK' if ok_plain else 'FAIL'} "
            f"(expected {cfg.expected_plain_id})"
        )

        methods = [e["method"] for e in client.request_log]
        if any(m != "GET" for m in methods):
            print(f"FAIL: non-GET methods: {methods}")
            return 4
        print()
        print(f"Requests: {len(methods)} GET-only")
        print(
            "Precondition: BEFORE WAZZUP_AUTO_REPLY_ENABLED=true, "
            "disable existing Wazzup/amoCRM autoresponse."
        )
        return 0 if (ok_transport and ok_state and ok_plain) else 5
    except WazzupTokenMissing as exc:
        print("Wazzup API: FAILED")
        print(str(exc))
        return 2
    except WazzupError as exc:
        print("Wazzup API: FAILED")
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
