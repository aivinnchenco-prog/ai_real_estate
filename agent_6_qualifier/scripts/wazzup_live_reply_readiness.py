#!/usr/bin/env python3
"""Read-only readiness check for controlled WhatsApp live auto-reply.

Never prints WAZZUP_API_KEY / bearer / full phone numbers.
Never PATCH / POST /v3/message.

Usage:
  cd agent_6_qualifier
  PYTHONPATH=src python3 scripts/wazzup_live_reply_readiness.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

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


def _mask_phone(raw: str) -> str:
    digits = "".join(c for c in str(raw) if c.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def _probe_url(url: str, *, method: str = "GET", timeout: float = 3.0) -> str:
    if not url.strip():
        return "missing"
    try:
        data = b"{}" if method.upper() == "POST" else None
        req = Request(url, method=method.upper(), data=data)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            code = int(resp.status)
            if 200 <= code < 300:
                return "reachable"
            return f"not reachable (HTTP {code})"
    except URLError as exc:
        return f"not reachable ({exc.reason or exc})"
    except Exception as exc:  # noqa: BLE001
        return f"not reachable ({type(exc).__name__})"


def _probe_local_webhook(local_health_url: str) -> str:
    primary = _probe_url(local_health_url, method="GET")
    if primary == "reachable":
        return primary
    # Fallback: safe Wazzup verification ping (no message send).
    base = local_health_url
    for suffix in ("/health", "/webhooks/wazzup/health"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    ping_url = base.rstrip("/") + "/webhooks/wazzup"
    try:
        req = Request(
            ping_url,
            method="POST",
            data=b'{"test":true}',
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        with urlopen(req, timeout=3.0) as resp:  # noqa: S310
            if 200 <= int(resp.status) < 300:
                return "reachable"
            return f"not reachable (HTTP {resp.status})"
    except Exception:  # noqa: BLE001
        return primary


def _webhook_uri_present(payload: Any) -> tuple[str, str]:
    """Return (CONFIGURED|FAIL, present|missing) without printing secrets/full URI host secrets."""
    if payload is None:
        return "FAIL", "missing"
    uri = ""
    if isinstance(payload, dict):
        uri = str(
            payload.get("webhooksUri")
            or payload.get("webhookUri")
            or payload.get("uri")
            or payload.get("url")
            or ""
        ).strip()
        nested = payload.get("webhooks") or payload.get("data")
        if not uri and isinstance(nested, dict):
            uri = str(
                nested.get("webhooksUri")
                or nested.get("webhookUri")
                or nested.get("uri")
                or nested.get("url")
                or ""
            ).strip()
        if not uri and isinstance(nested, list) and nested:
            first = nested[0]
            if isinstance(first, dict):
                uri = str(
                    first.get("webhooksUri")
                    or first.get("webhookUri")
                    or first.get("uri")
                    or first.get("url")
                    or ""
                ).strip()
    elif isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            uri = str(
                first.get("webhooksUri")
                or first.get("webhookUri")
                or first.get("uri")
                or first.get("url")
                or ""
            ).strip()
    if uri:
        return "CONFIGURED", "present"
    return "FAIL", "missing"


def _print_user_checklist() -> None:
    print()
    print("BEFORE LIVE ARM, USER MUST VERIFY:")
    print("1. WhatsApp Business App:")
    print("   Greeting Message = OFF")
    print("   Away Message = OFF")
    print("   или любые auto greetings OFF")
    print("2. Wazzup UI:")
    print("   no chatbot / auto-response / welcome automation")
    print("3. amoCRM:")
    print("   no Salesbot / automation that sends same greeting")
    print()
    print("Пользователь вручную подтверждает только:")
    print("  AUTORESPONSE_OFF=yes")
    print("После этого можно поставить:")
    print("  WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF=true")
    print("Не пытаться менять external UI через код.")


def main() -> int:
    _load_dotenv(_ROOT / ".env")

    from agent6_qualifier.messaging.ownership import ConversationOwnershipState
    from agent6_qualifier.messaging.wazzup_client import WazzupClient, normalize_channel
    from agent6_qualifier.messaging.wazzup_config import load_wazzup_config
    from agent6_qualifier.messaging.wazzup_errors import WazzupError, WazzupTokenMissing
    from agent6_qualifier.sessions import SessionStore

    cfg = load_wazzup_config()
    reasons: list[str] = []

    print("=== Wazzup live reply readiness (read-only) ===")
    print(f"API key:              {'SET' if cfg.api_key_set else 'MISSING'}")
    # Never print the key value.

    api_status = "FAIL"
    channel_status = "FAIL"
    webhook_status = "FAIL"
    webhook_url_status = "missing"

    if not cfg.api_key_set:
        reasons.append("WAZZUP_API_KEY missing")
        print("Wazzup API: FAIL")
    else:
        client = WazzupClient(cfg)
        try:
            raw_channels = client.get_channels()
            normalized = [normalize_channel(c) for c in raw_channels]
            api_status = "CONNECTED"
            print("Wazzup API: CONNECTED")
            expected = next(
                (c for c in normalized if c.get("channel_id") == cfg.channel_id),
                None,
            )
            if expected is None:
                channel_status = "FAIL"
                reasons.append("expected channel not found")
                print("Channel: FAIL (not found)")
            else:
                ok = (
                    (expected.get("transport") or "") == cfg.expected_transport
                    and (expected.get("state") or "") == cfg.expected_state
                )
                channel_status = "ACTIVE" if ok else "FAIL"
                print(f"Channel: {channel_status}")
                if not ok:
                    reasons.append(
                        f"channel transport/state mismatch "
                        f"(transport={expected.get('transport')} state={expected.get('state')})"
                    )
            try:
                wh = client.get_webhooks()
                webhook_status, webhook_url_status = _webhook_uri_present(wh)
            except WazzupError as exc:
                webhook_status = "FAIL"
                webhook_url_status = "missing"
                reasons.append(f"webhooks GET failed: {exc.code}")
            print(f"Webhook: {webhook_status}")
            print(f"Webhook URL: {webhook_url_status}")
            methods = [e["method"] for e in client.request_log]
            if any(m not in {"GET"} for m in methods):
                reasons.append(f"unexpected non-GET API methods: {methods}")
                print(f"API methods used: {methods} (expected GET-only)")
        except WazzupTokenMissing as exc:
            api_status = "FAIL"
            reasons.append(str(exc.code))
            print("Wazzup API: FAIL")
            print(str(exc))
        except WazzupError as exc:
            api_status = "FAIL"
            reasons.append(exc.code)
            print("Wazzup API: FAIL")
            print(str(exc))

    local_url = (
        os.getenv("WAZZUP_LOCAL_WEBHOOK_URL") or "http://127.0.0.1:8765/health"
    ).strip()
    public_url = (
        os.getenv("WAZZUP_PUBLIC_WEBHOOK_URL")
        or os.getenv("PUBLIC_WEBHOOK_URL")
        or ""
    ).strip()
    # Prefer health path for public probe if user gave webhook path.
    public_probe = public_url
    if public_probe.endswith("/webhooks/wazzup"):
        public_probe = public_probe[: -len("/webhooks/wazzup")] + "/health"

    local_reach = _probe_local_webhook(local_url)
    public_reach = _probe_url(public_probe, method="GET") if public_probe else "missing"

    print(f"Local webhook: {local_reach}")
    print(f"Public webhook: {public_reach}")

    print(f"SEND flag: {str(cfg.send_enabled).lower()}")
    print(f"AUTO_REPLY flag: {str(cfg.auto_reply_enabled).lower()}")
    print(
        "EXTERNAL_AUTORESPONSE_CONFIRMED_OFF: "
        f"{str(cfg.external_autoresponse_confirmed_off).lower()}"
    )
    print(
        f"ALLOWLIST: {'enabled' if cfg.live_allowlist_enabled else 'disabled'}"
    )
    print(f"Allowlisted phones count: {len(cfg.live_allowlist_phones)}")
    if cfg.live_allowlist_phones:
        masked = ", ".join(_mask_phone(p) for p in cfg.live_allowlist_phones[:5])
        extra = (
            f" (+{len(cfg.live_allowlist_phones) - 5} more)"
            if len(cfg.live_allowlist_phones) > 5
            else ""
        )
        print(f"Allowlist phones (masked): {masked}{extra}")
    print(f"Stage mode: {str(cfg.stage_mode).lower()}")
    print(f"WEBHOOK_ENABLED: {str(cfg.webhook_enabled).lower()}")

    # Ownership is in-process; readiness = import + construct.
    try:
        _ = ConversationOwnershipState(chat_id="readiness")
        ownership_status = "READY"
    except Exception as exc:  # noqa: BLE001
        ownership_status = "FAIL"
        reasons.append(f"ownership store: {type(exc).__name__}")
    print(f"Ownership store: {ownership_status}")

    session_root = _ROOT / "data" / "sessions"
    try:
        store = SessionStore(session_root)
        # touch path check without writing session contents
        session_root.mkdir(parents=True, exist_ok=True)
        _ = store
        session_status = "READY"
    except Exception as exc:  # noqa: BLE001
        session_status = "FAIL"
        reasons.append(f"session store: {type(exc).__name__}")
    print(f"Session store: {session_status}")

    # Live-arm conditions (report only — do not enable).
    if api_status != "CONNECTED":
        reasons.append("API not CONNECTED")
    if channel_status != "ACTIVE":
        reasons.append("Channel not ACTIVE")
    if not cfg.webhook_enabled:
        reasons.append("WAZZUP_WEBHOOK_ENABLED=false")
    if webhook_status != "CONFIGURED":
        reasons.append("Wazzup webhook not CONFIGURED")
    if not cfg.send_enabled:
        reasons.append("WAZZUP_SEND_ENABLED=false (safe default)")
    if not cfg.auto_reply_enabled:
        reasons.append("WAZZUP_AUTO_REPLY_ENABLED=false (safe default)")
    if not cfg.external_autoresponse_confirmed_off:
        reasons.append(
            "WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF=false "
            "(user must confirm autoresponse OFF)"
        )
    if not cfg.live_allowlist_enabled:
        reasons.append("WAZZUP_LIVE_ALLOWLIST_ENABLED=false")
    if cfg.live_allowlist_enabled and not cfg.live_allowlist_phones:
        reasons.append("allowlist enabled but empty")
    if ownership_status != "READY":
        reasons.append("ownership store FAIL")
    if session_status != "READY":
        reasons.append("session store FAIL")

    _print_user_checklist()

    print()
    # Ready for controlled live only when all live gates + infra are green.
    live_ready = (
        api_status == "CONNECTED"
        and channel_status == "ACTIVE"
        and cfg.webhook_enabled
        and webhook_status == "CONFIGURED"
        and cfg.send_enabled
        and cfg.auto_reply_enabled
        and cfg.external_autoresponse_confirmed_off
        and cfg.live_allowlist_enabled
        and bool(cfg.live_allowlist_phones)
        and ownership_status == "READY"
        and session_status == "READY"
    )
    if live_ready:
        print("Final: READY FOR CONTROLLED LIVE REPLY")
        return 0

    print("Final: BLOCKED")
    print("Reasons:")
    for r in reasons:
        print(f"  - {r}")
    # Expected interim state after this remediation: user still must confirm autoresponse.
    if (
        not cfg.external_autoresponse_confirmed_off
        and not cfg.send_enabled
        and not cfg.auto_reply_enabled
    ):
        print()
        print("Verdict note: READY FOR USER AUTORESPONSE CHECK")
        print(
            "(repo guard OK; keep SEND/AUTO_REPLY false until UI confirm + flag true)"
        )
    return 1


if __name__ == "__main__":
    # Ensure no accidental dump of env secrets in this process output path.
    raise SystemExit(main())
