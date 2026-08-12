"""Offline tests for production API deployment package (no network / no VPS)."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7_envoy.amo_chat.config import AmoChatChannelConfig, AmoChatConfig
from agent7_envoy.amo_chat.http_serve import build_request_handler
from agent7_envoy.amo_chat.production import (
    PRODUCTION_INTERNAL_HOST,
    PRODUCTION_INTERNAL_PORT,
    PRODUCTION_PUBLIC_BASE_URL,
    SAFE_LIVE_DEFAULTS,
    production_bind,
    production_registration_webhook_url,
)
from agent7_envoy.amo_chat.registration import build_registration_webhook_url
from agent7_envoy.amo_chat.webhook import AmoChatWebhookHandler


SECRET_FRAGMENTS = (
    "AMO_ACCESS_TOKEN",
    "CHANNEL_SECRET",
    "API_KEY",
    "BEGIN PRIVATE",
    "password",
)


def _cfg(tmp_path):
    return AmoChatConfig(
        amojo_base_url="https://amojo.amocrm.ru",
        account_id="acct-1",
        owner_silent_default=True,
        webhook_enabled=True,
        facebook=AmoChatChannelConfig(
            key="facebook",
            title="Open Home | Facebook Marketplace",
            channel_id="ch-fb",
            channel_secret="sec-fb",
            scope_id="scope-fb",
            bot_id="bot-fb",
            account_id="acct-1",
            webhook_enabled=True,
        ),
        airbnb=AmoChatChannelConfig(
            key="airbnb",
            title="Open Home | Airbnb",
            channel_id="ch-ab",
            channel_secret="sec-ab",
            scope_id="scope-ab",
            bot_id="bot-ab",
            account_id="acct-1",
            webhook_enabled=True,
        ),
        state_path=tmp_path / "state.json",
    )


def test_production_base_url_validation():
    assert PRODUCTION_PUBLIC_BASE_URL == "https://api.open-home.online"
    r = build_registration_webhook_url(PRODUCTION_PUBLIC_BASE_URL)
    assert r.ok
    assert r.url == production_registration_webhook_url()
    assert r.url == "https://api.open-home.online/webhooks/amo-chat/:scope_id"


def test_production_bind_loopback_only():
    assert production_bind() == "127.0.0.1:8000"
    assert PRODUCTION_INTERNAL_HOST == "127.0.0.1"
    assert PRODUCTION_INTERNAL_PORT == 8000


def test_nginx_config_template_ready():
    conf = (DEPLOY / "nginx" / "api.open-home.online.conf").read_text(encoding="utf-8")
    assert "server_name api.open-home.online;" in conf
    assert "127.0.0.1:8000" in conf
    assert "proxy_set_header Host $host;" in conf
    assert "X-Forwarded-Proto" in conf
    assert "client_max_body_size 2m;" in conf
    assert "listen 80" in conf
    # Certbot will add 443 later — do not hardcode missing cert paths
    assert "ssl_certificate" not in conf
    for frag in SECRET_FRAGMENTS:
        assert frag.lower() not in conf.lower()


def test_systemd_unit_no_secrets_and_loopback():
    unit = (DEPLOY / "systemd" / "openhome-api.service").read_text(encoding="utf-8")
    assert "AMO_CHAT_WEBHOOK_HOST=127.0.0.1" in unit
    assert "AMO_CHAT_WEBHOOK_PORT=8000" in unit
    assert "amo_chat_webhook_serve.py" in unit
    assert "REPLACE_VENV_PYTHON" in unit
    assert "REPLACE_ENV_FILE" in unit
    for frag in ("sk-", "eyJ", "Bearer ", "SECRET="):
        assert frag not in unit


def test_env_production_example_live_off():
    text = (DEPLOY / ".env.production.example").read_text(encoding="utf-8")
    assert "AMO_CHAT_PUBLIC_BASE_URL=https://api.open-home.online" in text
    for key, value in SAFE_LIVE_DEFAULTS.items():
        assert re.search(rf"^{re.escape(key)}={re.escape(value)}\s*$", text, re.M)
    # template must not contain filled secrets
    assert "AMO_ACCESS_TOKEN=\n" in text or "AMO_ACCESS_TOKEN=\r\n" in text or re.search(
        r"^AMO_ACCESS_TOKEN=\s*$", text, re.M
    )


def test_install_script_safe_defaults():
    script = (DEPLOY / "install_api_server.sh").read_text(encoding="utf-8")
    assert "nginx -t" in script
    assert "openhome-api" in script
    assert "playwright install" in script
    assert "ufw enable" not in script.split("Firewall")[0]  # not auto-enabled in main flow
    assert "AGENT7_LIVE_OUTREACH_ENABLED=true" not in script
    assert "AMO_CHAT_CONNECT_LIVE=true" not in script


def test_health_route_status_ok(tmp_path):
    handler = AmoChatWebhookHandler(_cfg(tmp_path), dry_run=True)
    httpd = HTTPServer(("127.0.0.1", 0), build_request_handler(handler))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode())
            assert body["status"] == "ok"
            assert body["ok"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_unknown_scope_http_404(tmp_path):
    handler = AmoChatWebhookHandler(_cfg(tmp_path), dry_run=True)
    httpd = HTTPServer(("127.0.0.1", 0), build_request_handler(handler))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = Request(
            f"http://127.0.0.1:{port}/webhooks/amo-chat/unknown",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as ei:
            urlopen(req, timeout=2)
        assert ei.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_malformed_json_does_not_kill_process(tmp_path):
    from agent7_envoy.amo_chat.signing import sign_request

    handler = AmoChatWebhookHandler(_cfg(tmp_path), dry_run=True)
    httpd = HTTPServer(("127.0.0.1", 0), build_request_handler(handler))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        raw = b"{not-json"
        path = "/webhooks/amo-chat/facebook"
        headers = sign_request(
            method="POST", body=raw, path=path, secret="sec-fb"
        )
        headers["Content-Type"] = "application/json"
        req = Request(
            f"http://127.0.0.1:{port}{path}",
            data=raw,
            method="POST",
            headers=headers,
        )
        try:
            urlopen(req, timeout=2)
            raised = False
        except HTTPError as exc:
            raised = True
            assert exc.code in {400, 502}
        assert raised
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
            assert resp.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_safe_live_defaults_remain_off(monkeypatch):
    for key, value in SAFE_LIVE_DEFAULTS.items():
        monkeypatch.setenv(key, value)
    assert os.getenv("AGENT7_LIVE_OUTREACH_ENABLED") == "false"
    assert os.getenv("AMO_CHAT_WEBHOOK_ENABLED") == "false"
    assert os.getenv("AMO_CHAT_MIRROR_LIVE") == "false"
    assert os.getenv("AMO_CHAT_CONNECT_LIVE") == "false"
