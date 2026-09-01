"""Wazzup config + phone helpers (no secrets logged)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


DEFAULT_WAZZUP_CHANNEL_ID = "7ac4092a-cf3c-450f-8518-d7cc7bd3f995"
DEFAULT_WAZZUP_API_BASE_URL = "https://api.wazzup24.com"
EXPECTED_TRANSPORT = "whatsapp"
EXPECTED_STATE = "active"
EXPECTED_PLAIN_ID = "66625124002"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class WazzupConfig:
    api_key: str = ""
    channel_id: str = DEFAULT_WAZZUP_CHANNEL_ID
    api_base_url: str = DEFAULT_WAZZUP_API_BASE_URL
    send_enabled: bool = False
    webhook_enabled: bool = False
    auto_reply_enabled: bool = False
    # User-confirmed: WA Business / Wazzup / amoCRM auto-greetings are OFF.
    # Default false — live Agent6 POST requires explicit true.
    external_autoresponse_confirmed_off: bool = False
    request_timeout_seconds: float = 15.0
    webhook_bearer: str = ""  # optional; if set, require Authorization match
    expected_transport: str = EXPECTED_TRANSPORT
    expected_state: str = EXPECTED_STATE
    expected_plain_id: str = EXPECTED_PLAIN_ID
    get_max_retries: int = 2
    live_allowlist_enabled: bool = True
    live_allowlist_phones: tuple[str, ...] = ()
    stage_mode: bool = True
    debounce_sec: float = 4.0

    @property
    def api_key_set(self) -> bool:
        return bool(self.api_key.strip())

    def phone_allowed_for_live(self, phone: str | None) -> bool:
        """When allowlist enabled, only listed E.164 digits may receive live bot POST."""
        if not self.live_allowlist_enabled:
            return True
        digits = normalize_phone_e164_digits(phone)
        if not digits:
            return False
        allowed = {
            normalize_phone_e164_digits(p)
            for p in self.live_allowlist_phones
            if normalize_phone_e164_digits(p)
        }
        return digits in allowed

    def redacted_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "api_base_url": self.api_base_url,
            "api_key": "SET" if self.api_key_set else "MISSING",
            "send_enabled": self.send_enabled,
            "webhook_enabled": self.webhook_enabled,
            "auto_reply_enabled": self.auto_reply_enabled,
            "external_autoresponse_confirmed_off": (
                self.external_autoresponse_confirmed_off
            ),
            "request_timeout_seconds": self.request_timeout_seconds,
            "webhook_bearer": "SET" if self.webhook_bearer.strip() else "MISSING",
            "live_allowlist_enabled": self.live_allowlist_enabled,
            "live_allowlist_count": len(self.live_allowlist_phones),
            "stage_mode": self.stage_mode,
            "debounce_sec": self.debounce_sec,
        }


def _parse_phone_list(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    parts = []
    for chunk in str(raw).replace(";", ",").split(","):
        item = chunk.strip()
        if item:
            parts.append(item)
    return tuple(parts)


def load_wazzup_config() -> WazzupConfig:
    return WazzupConfig(
        api_key=(os.getenv("WAZZUP_API_KEY") or "").strip(),
        channel_id=(
            os.getenv("WAZZUP_CHANNEL_ID") or DEFAULT_WAZZUP_CHANNEL_ID
        ).strip(),
        api_base_url=(
            os.getenv("WAZZUP_API_BASE_URL") or DEFAULT_WAZZUP_API_BASE_URL
        ).rstrip("/"),
        send_enabled=_env_bool("WAZZUP_SEND_ENABLED", False),
        webhook_enabled=_env_bool("WAZZUP_WEBHOOK_ENABLED", False),
        auto_reply_enabled=_env_bool("WAZZUP_AUTO_REPLY_ENABLED", False),
        external_autoresponse_confirmed_off=_env_bool(
            "WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", False
        ),
        request_timeout_seconds=_env_float("WAZZUP_REQUEST_TIMEOUT_SECONDS", 15.0),
        webhook_bearer=(os.getenv("WAZZUP_WEBHOOK_BEARER") or "").strip(),
        get_max_retries=int(os.getenv("WAZZUP_GET_MAX_RETRIES") or "2"),
        live_allowlist_enabled=_env_bool("WAZZUP_LIVE_ALLOWLIST_ENABLED", True),
        live_allowlist_phones=_parse_phone_list(
            os.getenv("WAZZUP_LIVE_ALLOWLIST_PHONES")
        ),
        stage_mode=_env_bool("WAZZUP_STAGE_MODE", True),
        debounce_sec=_env_float("WAZZUP_DEBOUNCE_SEC", 4.0),
    )



_NON_DIGIT_RE = re.compile(r"\D+")


def normalize_phone_e164_digits(raw: str | None, *, default_cc: str = "66") -> str | None:
    """Normalize phone to digits-only international form (no +).

    Thailand: local 0XXXXXXXXX → 66XXXXXXXXX; already 66… kept.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    digits = _NON_DIGIT_RE.sub("", text)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if default_cc == "66":
        if digits.startswith("0") and len(digits) >= 9:
            digits = "66" + digits[1:]
        elif len(digits) == 9 and digits.startswith("6"):
            # already without leading 0 sometimes
            digits = "66" + digits
    return digits
