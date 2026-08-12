#!/usr/bin/env python3
"""Open Home production readiness report (no secrets)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _probe(url: str, timeout: float = 2.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return int(getattr(resp, "status", 200) or 200) == 200
    except (URLError, OSError, ValueError):
        return False


def _channel_lane(key: str) -> str:
    from agent7_envoy.amo_chat.channel_config import validate_channel_config

    p = validate_channel_config()[key]
    if not p.channel_id and not p.channel_secret and not p.bot_id:
        return "NOT_REGISTERED"
    if p.connected:
        return "CONNECTED"
    if p.credentials_ready:
        return "CONFIGURED"
    return "PARTIAL"


def _registration_stage(fb: str, ab: str) -> str:
    if fb == "CONNECTED" and ab == "CONNECTED":
        return "APPROVED"
    if fb in {"CONFIGURED", "CONNECTED", "PARTIAL"} or ab in {
        "CONFIGURED",
        "CONNECTED",
        "PARTIAL",
    }:
        return "SENT"
    return "NOT_SENT"


def _gate_lane(*, enabled: bool, ready: bool) -> str:
    if enabled:
        return "LIVE"
    if ready:
        return "READY"
    return "OFF"


def main() -> int:
    _load_dotenv(REPO / ".env")
    _load_dotenv(ROOT / ".env")
    _load_dotenv(Path(os.getenv("OPENHOME_ENV_FILE") or "/opt/openhome/.env"))

    from agent7_envoy.amo_chat.dns_readiness import check_dns
    from agent7_envoy.amo_chat.production import (
        PRODUCTION_INTERNAL_HOST,
        PRODUCTION_INTERNAL_PORT,
        PRODUCTION_PUBLIC_BASE_URL,
        production_bind,
        production_registration_webhook_url,
    )
    from agent7_envoy.amo_chat.registration import (
        build_registration_webhook_url,
        public_base_url_from_env,
        resolve_client_uuid,
    )
    from agent7_envoy.live_gates import any_live_enabled, read_live_gates

    public = public_base_url_from_env() or PRODUCTION_PUBLIC_BASE_URL
    webhook = build_registration_webhook_url(public)
    webhook_url = webhook.url if webhook.ok else production_registration_webhook_url()

    host = os.getenv("AMO_CHAT_WEBHOOK_HOST") or PRODUCTION_INTERNAL_HOST
    port = os.getenv("AMO_CHAT_WEBHOOK_PORT") or str(PRODUCTION_INTERNAL_PORT)
    bind = f"{host}:{port}" if host and port else production_bind()

    dns = check_dns()
    https_ok = _probe(f"{PRODUCTION_PUBLIC_BASE_URL}/health", timeout=3.0)
    local_ok = _probe(f"http://{host}:{port}/health")
    health_ok = local_ok or https_ok

    amo_account = "UNKNOWN"
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from amo_chat_account_info import fetch_account_info

        info = fetch_account_info()
        uuid_ok = resolve_client_uuid().match
        amo_account = "READY" if info.ready and uuid_ok else "FAIL"
    except Exception:
        if os.getenv("AMO_SUBDOMAIN") and os.getenv("AMO_ACCESS_TOKEN"):
            amo_account = "FAIL"
        else:
            amo_account = "NOT CONFIGURED"

    fb = _channel_lane("facebook")
    ab = _channel_lane("airbnb")
    reg = _registration_stage(fb, ab)

    webhook_enabled = _env_bool("AMO_CHAT_WEBHOOK_ENABLED", False)
    mirror_enabled = _env_bool("AMO_CHAT_MIRROR_LIVE", False)
    agent7_enabled = (
        _env_bool("AGENT7_LIVE_OUTREACH_ENABLED", False)
        or _env_bool("AGENT7_FACEBOOK_MESSENGER_ENABLED", False)
        or _env_bool("AGENT7_AIRBNB_MESSAGES_ENABLED", False)
    )

    webhook_lane = _gate_lane(
        enabled=webhook_enabled, ready=fb == "CONNECTED" or ab == "CONNECTED"
    )
    mirror_lane = _gate_lane(
        enabled=mirror_enabled, ready=fb == "CONNECTED" or ab == "CONNECTED"
    )
    agent7_lane = _gate_lane(
        enabled=agent7_enabled, ready=False
    )  # READY only after explicit ops approval later

    rollback_docs = (REPO / "deploy" / "ROLLBACK.md").exists() or (
        Path("/opt/openhome/app/deploy/ROLLBACK.md")
    ).exists()
    rollback = "READY" if rollback_docs and not any_live_enabled(read_live_gates()) else (
        "READY" if rollback_docs else "NOT_READY"
    )

    print("OPEN HOME PRODUCTION STATUS")
    print()
    print("DNS:")
    print("READY" if dns.ready else "WAITING")
    print()
    print("HTTPS:")
    print("READY" if https_ok else "WAITING")
    print()
    print("PUBLIC BASE URL:")
    print(public)
    print()
    print("WEBHOOK URL:")
    print(webhook_url)
    print()
    print("APP BIND:")
    print(bind)
    print()
    print("HEALTH:")
    print("PASS" if health_ok else "FAIL")
    print()
    print("AMO ACCOUNT:")
    print(amo_account)
    print()
    print("AMO REGISTRATION:")
    print(reg)
    print()
    print("AMO FB CHANNEL:")
    print(fb)
    print()
    print("AMO AIRBNB CHANNEL:")
    print(ab)
    print()
    print("WEBHOOK:")
    print(webhook_lane)
    print()
    print("MIRROR:")
    print(mirror_lane)
    print()
    print("AGENT7 SOURCE_NATIVE:")
    print(agent7_lane)
    print()
    print("ROLLBACK:")
    print(rollback)
    return 0 if (not any_live_enabled(read_live_gates())) else 2


if __name__ == "__main__":
    raise SystemExit(main())
