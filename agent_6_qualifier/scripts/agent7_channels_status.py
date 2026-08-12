#!/usr/bin/env python3
"""Agent7 owner-channel status diagnostic (no live sends)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent7_envoy.messaging.airbnb_messages import AirbnbMessagesOwnerTransport  # noqa: E402
from agent7_envoy.messaging.base import (  # noqa: E402
    agent7_live_enabled,
    airbnb_messages_enabled,
    facebook_messenger_enabled,
)
from agent7_envoy.messaging.facebook_messenger import (  # noqa: E402
    FacebookMessengerOwnerTransport,
)
from agent7_envoy.messaging.telegram import TelegramOwnerTransport  # noqa: E402
from agent7_envoy.messaging.whatsapp import WhatsAppOwnerTransport  # noqa: E402


def _fb_line(hc) -> str:
    if not hc.channel_enabled:
        return "DISABLED"
    if hc.status.value == "READY":
        return f"AUTH READY ({hc.profile_dir})"
    if hc.status.value in {"AUTH_REQUIRED", "SESSION_EXPIRED"}:
        return f"AUTH REQUIRED ({hc.profile_dir})"
    return f"{hc.status.value} ({hc.profile_dir})"


def _airbnb_line(hc) -> str:
    if not hc.channel_enabled:
        return "DISABLED"
    if hc.status.value == "READY":
        return "AUTH READY"
    if hc.status.value == "RATE_LIMITED":
        return "RATE_LIMITED"
    if hc.status.value in {"AUTH_REQUIRED", "SESSION_EXPIRED"}:
        return "AUTH REQUIRED"
    return hc.status.value


def _amo_block(key: str, ch) -> None:
    print(f"  amo chat channel: {'CONFIGURED' if ch.configured else 'NOT_CONFIGURED'}")
    print(f"  scope: {'CONNECTED' if ch.connected else 'NOT_CONNECTED'}")
    if ch.ready:
        mirror = "READY"
    elif ch.configured:
        mirror = "DEGRADED"
    else:
        mirror = "DEGRADED"
    print(f"  mirror: {mirror}")


def main() -> int:
    wa = WhatsAppOwnerTransport().healthcheck()
    tg = TelegramOwnerTransport().healthcheck()
    fb = FacebookMessengerOwnerTransport().healthcheck()
    ab = AirbnbMessagesOwnerTransport().healthcheck()

    print("WHATSAPP:")
    print(f"  {'READY' if wa.ready else 'NOT READY'} ({wa.detail or wa.status.value})")
    print("TELEGRAM:")
    print(f"  {'READY' if tg.ready else 'NOT READY'} ({tg.detail or tg.status.value})")
    print("FACEBOOK MESSENGER:")
    print(f"  transport: {_fb_line(fb)}")
    print(f"  live gate: {facebook_messenger_enabled()}")
    print("AIRBNB MESSAGES:")
    print(f"  transport: {_airbnb_line(ab)}")
    print(f"  live gate: {airbnb_messages_enabled()}")
    print("LIVE:")
    print(f"  {'ON' if agent7_live_enabled() else 'OFF'}")

    try:
        from agent7_envoy.amo_chat.config import load_amo_chat_config

        cfg = load_amo_chat_config()
        print("FACEBOOK AMO CHAT:")
        _amo_block("facebook", cfg.facebook)
        print("AIRBNB AMO CHAT:")
        _amo_block("airbnb", cfg.airbnb)
        print("AMO CHAT WEBHOOK:")
        print(f"  {'READY' if cfg.webhook_enabled else 'OFF'}")
        print(f"  owner_silent={cfg.owner_silent_default}")
    except Exception as exc:
        print("AMO CHAT:")
        print(f"  UNAVAILABLE ({type(exc).__name__})")

    print()
    print(f"FACEBOOK_OWNER_MESSAGING: {fb.status.value}")
    print(f"AIRBNB_OWNER_MESSAGING: {ab.status.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
