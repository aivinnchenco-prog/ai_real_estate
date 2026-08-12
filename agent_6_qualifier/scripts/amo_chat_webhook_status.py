#!/usr/bin/env python3
"""Local/public readiness for amoCRM Chat API webhook endpoint (no tunnels)."""

from __future__ import annotations

import os
import sys
import threading
from http.server import HTTPServer
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


def _probe_health(url: str, timeout: float = 2.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310 — local health only
            return int(getattr(resp, "status", 200) or 200) == 200
    except (URLError, OSError, ValueError):
        return False


def _ephemeral_health_ok(host: str) -> bool:
    """Spin a short-lived server on an ephemeral port and hit /health."""
    from agent7_envoy.amo_chat.config import load_amo_chat_config
    from agent7_envoy.amo_chat.http_serve import build_request_handler
    from agent7_envoy.amo_chat.webhook import AmoChatWebhookHandler

    cfg = load_amo_chat_config()
    handler = AmoChatWebhookHandler(cfg, dry_run=True)
    httpd = HTTPServer((host, 0), build_request_handler(handler))
    bound_port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        ok = _probe_health(f"http://{host}:{bound_port}/health")
        ok2 = _probe_health(f"http://{host}:{bound_port}/webhooks/amo-chat/health")
        return ok and ok2
    finally:
        httpd.shutdown()
        httpd.server_close()


def main() -> int:
    _load_dotenv(REPO / ".env")
    _load_dotenv(ROOT / ".env")

    from agent7_envoy.amo_chat.registration import (
        build_registration_webhook_url,
        local_webhook_base_url,
        public_base_url_from_env,
    )

    local_url = local_webhook_base_url()
    host = os.getenv("AMO_CHAT_WEBHOOK_HOST", "127.0.0.1") or "127.0.0.1"
    # Prefer existing local server; otherwise ephemeral probe proves code readiness.
    local_ready = _probe_health(f"{local_url}/health")
    if not local_ready:
        try:
            local_ready = _ephemeral_health_ok(host)
        except Exception:
            local_ready = False

    public = public_base_url_from_env()
    public_line = public if public else "NOT CONFIGURED"
    built = build_registration_webhook_url(public)
    if built.ok:
        registration = built.url
    else:
        registration = "NOT READY"

    webhook_server = "READY" if local_ready else "BLOCKED"

    print("WEBHOOK SERVER:")
    print(webhook_server)
    print()
    print("LOCAL URL:")
    print(local_url)
    print()
    print("PUBLIC BASE URL:")
    print(public_line)
    print()
    print("REGISTRATION WEBHOOK:")
    print(registration)
    return 0 if local_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
