#!/usr/bin/env python3
"""amoCRM custom chat channels setup for Agent7 (FB + Airbnb).

Commands:
  status
  connect-facebook
  connect-airbnb
  verify
  dry-run-facebook
  dry-run-airbnb

Channel registration (channel_id + secret) requires amoCRM support —
this script only connects an already-registered channel and dry-runs signing.
Never prints secrets.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


def _print_status() -> int:
    from agent7_envoy.amo_chat.config import load_amo_chat_config

    cfg = load_amo_chat_config()
    print(json.dumps(cfg.redacted(), ensure_ascii=False, indent=2))
    print()
    print("SETUP NOTES:")
    print("1) Register TWO private chat channels via amoCRM support:")
    print("   - Open Home | Facebook Marketplace")
    print("   - Open Home | Airbnb")
    print("2) Put channel_id / channel_secret / bot_id into local .env")
    print("3) Set AMO_CHAT_ACCOUNT_ID from GET /api/v4/account?with=amojo_id")
    print("4) Run: connect-facebook / connect-airbnb to obtain scope_id")
    print("5) Webhook URL form: https://YOUR_HOST/webhooks/amo-chat/:scope_id")
    print("6) Keep AMO_CHAT_OWNER_SILENT=true to avoid Неразобранное client leads")
    return 0


def _connect(key: str) -> int:
    from agent7_envoy.amo_chat.client import AmojoChatClient
    from agent7_envoy.amo_chat.config import load_amo_chat_config, save_channel_scope

    cfg = load_amo_chat_config()
    ch = cfg.channel(key)
    if not ch.configured:
        print(f"NOT_CONFIGURED: set AMO_CHAT_{key.upper()}_CHANNEL_ID/SECRET in .env")
        return 2
    if not cfg.account_id:
        print("MISSING: AMO_CHAT_ACCOUNT_ID (amojo_id from /api/v4/account?with=amojo_id)")
        return 3
    if ch.connected:
        print(f"ALREADY_CONNECTED scope=SET (idempotent skip)")
        return 0

    live = os.getenv("AMO_CHAT_CONNECT_LIVE", "false").lower() in {"1", "true", "yes"}
    client = AmojoChatClient(cfg, dry_run=not live)
    resp = client.connect_channel(ch)
    if not live:
        print("DRY_RUN connect payload prepared (set AMO_CHAT_CONNECT_LIVE=true to call amojo)")
        print(json.dumps({"ok": resp.ok, "data_keys": list(resp.data.keys())}, indent=2))
        return 0
    if not resp.ok:
        print(f"CONNECT_FAILED status={resp.status_code} error={resp.error}")
        return 4
    scope = str(resp.data.get("scope_id") or "")
    if not scope:
        print("CONNECT_FAILED: no scope_id in response")
        return 5
    save_channel_scope(
        channel_key=key, scope_id=scope, account_id=cfg.account_id, bot_id=ch.bot_id
    )
    print(f"CONNECTED: scope saved to {cfg.state_path} (secret not written)")
    return 0


def _verify() -> int:
    from agent7_envoy.amo_chat.config import load_amo_chat_config
    from agent7_envoy.amo_chat.signing import content_md5, sign_request, verify_signature

    cfg = load_amo_chat_config()
    body = b'{"account_id":"test","title":"t","hook_api_version":"v2"}'
    path = "/v2/origin/custom/test-channel/connect"
    # Use a disposable secret for offline verify of algorithm only
    secret = "unit-test-secret"
    headers = sign_request(method="POST", body=body, path=path, secret=secret)
    ok = verify_signature(
        method="POST", body=body, path=path, secret=secret, headers=headers
    )
    print(f"SIGNING: {'PASS' if ok else 'FAIL'}")
    print(f"Content-MD5: {content_md5(body)}")
    print("FACEBOOK:", "CONFIGURED" if cfg.facebook.configured else "NOT_CONFIGURED",
          "/", "CONNECTED" if cfg.facebook.connected else "NOT_CONNECTED")
    print("AIRBNB:", "CONFIGURED" if cfg.airbnb.configured else "NOT_CONFIGURED",
          "/", "CONNECTED" if cfg.airbnb.connected else "NOT_CONNECTED")
    print("OWNER_SILENT_DEFAULT:", cfg.owner_silent_default)
    print("WEBHOOK:", "ON" if cfg.webhook_enabled else "OFF")
    return 0 if ok else 1


def _dry_run(key: str) -> int:
    from agent7_envoy.amo_chat.mirror import AmoChatMirrorService
    from agent7_envoy.amo_chat.origin import MessageOrigin

    svc = AmoChatMirrorService(dry_run=True)
    r = svc.mirror_source_message(
        channel=key,
        owner_request_id="orq-dry-run",
        object_id="OBJ_DRY",
        text="dry-run mirror text",
        origin=MessageOrigin.SOURCE_NATIVE_OUTBOUND,
        external_thread_id="thread-dry",
        source_url="https://example.com/listing",
        external_message_id="ext-dry-1",
    )
    print(json.dumps({
        "ok": r.ok,
        "degraded": r.degraded,
        "skipped": r.skipped,
        "reason": r.reason,
        "conversation_id": r.conversation_id,
        "msgid": r.msgid,
        "notes": r.notes,
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(ROOT / ".env")
    args = list(argv or sys.argv[1:])
    cmd = (args[0] if args else "status").strip().lower()
    if cmd in {"status", "s"}:
        return _print_status()
    if cmd in {"connect-facebook", "connect-fb"}:
        return _connect("facebook")
    if cmd in {"connect-airbnb", "connect-ab"}:
        return _connect("airbnb")
    if cmd == "verify":
        return _verify()
    if cmd in {"dry-run-facebook", "dry-run-fb"}:
        return _dry_run("facebook")
    if cmd in {"dry-run-airbnb", "dry-run-ab"}:
        return _dry_run("airbnb")
    print("Usage: amo_chat_setup.py status|connect-facebook|connect-airbnb|verify|dry-run-facebook|dry-run-airbnb")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
