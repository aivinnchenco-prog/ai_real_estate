"""Canonical live/outbound gates for Open Home multi-agent system.

All outbound paths must require at least one of these flags (defaults OFF).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


# name, default, purpose
LIVE_GATE_SPECS: tuple[tuple[str, bool, str], ...] = (
    ("AGENT7_LIVE_OUTREACH_ENABLED", False, "Master Agent7 outbound (WA/TG/FB/Airbnb)"),
    ("AGENT7_FACEBOOK_MESSENGER_ENABLED", False, "Facebook Messenger / Marketplace source-native send"),
    ("AGENT7_AIRBNB_MESSAGES_ENABLED", False, "Airbnb Messages source-native send"),
    ("AMO_CHAT_WEBHOOK_ENABLED", False, "Accept amo Chat API webhooks (manager→source)"),
    ("AMO_CHAT_MIRROR_LIVE", False, "Live amojo import of source messages"),
    ("AMO_CHAT_CONNECT_LIVE", False, "Live amojo channel connect (scope_id)"),
    ("WAZZUP_SEND_ENABLED", False, "WhatsApp/Wazzup network POST"),
    ("WAZZUP_AUTO_REPLY_ENABLED", False, "WhatsApp auto-reply content allowed to send"),
    ("WAZZUP_WEBHOOK_ENABLED", False, "WhatsApp inbound webhook server"),
)


@dataclass(frozen=True)
class LiveGate:
    name: str
    enabled: bool
    default: bool
    purpose: str

    @property
    def safe(self) -> bool:
        return not self.enabled


def read_live_gates() -> list[LiveGate]:
    out: list[LiveGate] = []
    for name, default, purpose in LIVE_GATE_SPECS:
        out.append(
            LiveGate(
                name=name,
                enabled=_env_bool(name, default),
                default=default,
                purpose=purpose,
            )
        )
    return out


def any_live_enabled(gates: Iterable[LiveGate] | None = None) -> bool:
    items = list(gates) if gates is not None else read_live_gates()
    return any(g.enabled for g in items)


def format_live_gate_matrix(gates: Iterable[LiveGate] | None = None) -> str:
    items = list(gates) if gates is not None else read_live_gates()
    lines = ["LIVE GATE MATRIX", ""]
    for g in items:
        state = "ON" if g.enabled else "OFF"
        lines.append(f"{g.name}: {state}")
        lines.append(f"  {g.purpose}")
    lines.append("")
    lines.append(f"OVERALL SAFE: {'YES' if not any_live_enabled(items) else 'NO'}")
    return "\n".join(lines)


def source_native_send_allowed(channel: str) -> bool:
    """FB/Airbnb source-native requires master + channel gate."""
    if not _env_bool("AGENT7_LIVE_OUTREACH_ENABLED", False):
        return False
    ch = (channel or "").strip().lower()
    if ch in {"facebook", "facebook_messenger", "fb", "fb_marketplace"}:
        return _env_bool("AGENT7_FACEBOOK_MESSENGER_ENABLED", False)
    if ch in {"airbnb", "airbnb_messages"}:
        return _env_bool("AGENT7_AIRBNB_MESSAGES_ENABLED", False)
    # WA/TG use master gate only (plus their own transports' Wazzup/Telethon checks)
    return True
