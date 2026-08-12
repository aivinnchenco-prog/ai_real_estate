#!/usr/bin/env python3
"""Serve amoCRM Chat API webhooks for FB/Airbnb custom owner channels.

Routes:
  GET  /health
  GET  /webhooks/amo-chat/health
  POST /webhooks/amo-chat/facebook
  POST /webhooks/amo-chat/airbnb
  POST /webhooks/amo-chat/<scope_id>   (official :scope_id form)

Live source sends remain gated by Agent7 live flags (default OFF).
POST handling still respects AMO_CHAT_WEBHOOK_ENABLED inside the handler.
Server may start with the flag off so local /health stays available.
"""

from __future__ import annotations

import os
import sys
from http.server import HTTPServer
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


def main() -> int:
    _load_dotenv(ROOT.parents[0] / ".env")
    _load_dotenv(ROOT / ".env")
    _load_dotenv(Path(os.getenv("OPENHOME_ENV_FILE") or "/opt/openhome/.env"))
    from agent7_envoy.amo_chat.config import load_amo_chat_config
    from agent7_envoy.amo_chat.http_serve import build_request_handler
    from agent7_envoy.amo_chat.webhook import AmoChatWebhookHandler

    from agent7_envoy.amo_chat.production import (
        PRODUCTION_INTERNAL_HOST,
        PRODUCTION_INTERNAL_PORT,
    )

    cfg = load_amo_chat_config()
    # Always dry_run at the HTTP edge — live source send needs Agent7 gates + transport.
    handler = AmoChatWebhookHandler(cfg, dry_run=True)

    # Production bind: 127.0.0.1:8000 behind NGINX. Local default remains :8766.
    prod = (os.getenv("OPENHOME_API_PRODUCTION") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    default_host = PRODUCTION_INTERNAL_HOST if prod else "127.0.0.1"
    default_port = str(PRODUCTION_INTERNAL_PORT) if prod else "8766"
    host = os.getenv("AMO_CHAT_WEBHOOK_HOST", default_host) or default_host
    port = int(os.getenv("AMO_CHAT_WEBHOOK_PORT", default_port) or default_port)

    if host not in {"127.0.0.1", "localhost", "::1"} and prod:
        print("REFUSE: production API must bind loopback only (127.0.0.1)")
        return 4

    if not cfg.webhook_enabled:
        print(
            "WARN: AMO_CHAT_WEBHOOK_ENABLED=false — /health available; "
            "POST payloads ignored by handler"
        )

    httpd = HTTPServer((host, port), build_request_handler(handler))
    print(f"openhome api on http://{host}:{port} (dry_run source sends)")
    print(f"health: http://{host}:{port}/health")
    print(f"amo-chat: http://{host}:{port}/webhooks/amo-chat/:scope_id")
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
