"""Offline tests for amoCRM chat registration helpers + webhook routing."""

from __future__ import annotations

import json
import sys
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7_envoy.amo_chat.config import AmoChatChannelConfig, AmoChatConfig
from agent7_envoy.amo_chat.http_serve import build_request_handler
from agent7_envoy.amo_chat.origin import MessageOrigin, should_send_to_source
from agent7_envoy.amo_chat.registration import (
    EXPECTED_CLIENT_UUID,
    build_registration_webhook_url,
    normalize_public_base_url,
    parse_account_payload,
    require_amojo_id,
    resolve_client_uuid,
)
from agent7_envoy.amo_chat.routing import resolve_webhook_channel
from agent7_envoy.amo_chat.webhook import AmoChatWebhookHandler


def _cfg(tmp_path, *, fb_scope="scope-fb", ab_scope="scope-ab"):
    return AmoChatConfig(
        amojo_base_url="https://amojo.amocrm.ru",
        account_id="acct-1",
        owner_silent_default=True,
        webhook_enabled=True,
        airbnb_enabled=True,
        facebook=AmoChatChannelConfig(
            key="facebook",
            title="Open Home | Facebook Marketplace",
            channel_id="ch-fb",
            channel_secret="sec-fb",
            scope_id=fb_scope,
            bot_id="bot-fb",
            account_id="acct-1",
            webhook_enabled=True,
        ),
        airbnb=AmoChatChannelConfig(
            key="airbnb",
            title="Open Home | Airbnb",
            channel_id="ch-ab",
            channel_secret="sec-ab",
            scope_id=ab_scope,
            bot_id="bot-ab",
            account_id="acct-1",
            webhook_enabled=True,
        ),
        state_path=tmp_path / "state.json",
    )


def test_account_info_parser_extracts_ids():
    info = parse_account_payload(
        {
            "id": 33148394,
            "name": "Open Home",
            "subdomain": "aivinnchenco",
            "amojo_id": "amojo-uuid-1",
        }
    )
    assert info.account_id == "33148394"
    assert info.amojo_id == "amojo-uuid-1"
    assert info.subdomain == "aivinnchenco"
    assert info.ready is True


def test_amojo_id_extraction_from_embedded():
    info = parse_account_payload(
        {"id": 1, "subdomain": "x", "_embedded": {"amojo_id": "embedded-uuid"}}
    )
    assert info.amojo_id == "embedded-uuid"


def test_missing_amojo_id_raises():
    info = parse_account_payload({"id": 1, "subdomain": "x"})
    assert info.amojo_id == ""
    with pytest.raises(ValueError, match="amojo_id"):
        require_amojo_id(info)


def test_wrong_integration_uuid_detection():
    check = resolve_client_uuid(configured="00000000-0000-0000-0000-000000000000")
    assert check.match is False
    assert check.source == "mismatch"
    assert check.expected == EXPECTED_CLIENT_UUID


def test_expected_client_uuid_match():
    check = resolve_client_uuid(configured=EXPECTED_CLIENT_UUID)
    assert check.match is True
    assert check.current == EXPECTED_CLIENT_UUID


def test_webhook_url_builder_https_accepted():
    r = build_registration_webhook_url("https://hooks.example.com")
    assert r.ok
    assert r.url == "https://hooks.example.com/webhooks/amo-chat/:scope_id"


def test_trailing_slash_normalization():
    assert normalize_public_base_url("https://hooks.example.com/") == "https://hooks.example.com"
    r = build_registration_webhook_url("https://hooks.example.com/")
    assert r.url == "https://hooks.example.com/webhooks/amo-chat/:scope_id"


def test_invalid_public_url_rejected():
    assert build_registration_webhook_url("not-a-url").ok is False
    assert build_registration_webhook_url("ftp://hooks.example.com").reason == "invalid_public_url"


def test_http_url_rejected_for_production_registration():
    r = build_registration_webhook_url("http://hooks.example.com")
    assert r.ok is False
    assert r.reason == "http_rejected"


def test_https_url_accepted_with_path_prefix():
    r = build_registration_webhook_url("https://hooks.example.com/apps/openhome")
    assert r.ok
    assert r.url == "https://hooks.example.com/apps/openhome/webhooks/amo-chat/:scope_id"


def test_scope_path_preserved():
    r = build_registration_webhook_url(
        "https://example.com", scope_placeholder="abc-scope"
    )
    assert r.url.endswith("/webhooks/amo-chat/abc-scope")


def test_empty_public_url_not_configured():
    r = build_registration_webhook_url("")
    assert r.ok is False
    assert r.reason == "not_configured"


def test_webhook_health_route(tmp_path):
    cfg = _cfg(tmp_path)
    handler = AmoChatWebhookHandler(cfg, dry_run=True)
    httpd = HTTPServer(("127.0.0.1", 0), build_request_handler(handler))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode())
            assert body["ok"] is True
            assert body.get("status") == "ok"
        with urlopen(
            f"http://127.0.0.1:{port}/webhooks/amo-chat/health", timeout=2
        ) as resp:
            assert resp.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_channel_routing_by_name_and_scope(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="scope-fb", ab_scope="scope-ab")
    assert resolve_webhook_channel("/webhooks/amo-chat/facebook", cfg).channel_key == "facebook"
    assert resolve_webhook_channel("/webhooks/amo-chat/airbnb", cfg).channel_key == "airbnb"
    assert resolve_webhook_channel("/webhooks/amo-chat/scope-fb", cfg).channel_key == "facebook"
    assert resolve_webhook_channel("/webhooks/amo-chat/scope-ab", cfg).channel_key == "airbnb"


def test_unknown_scope_rejected(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="scope-fb", ab_scope="scope-ab")
    r = resolve_webhook_channel("/webhooks/amo-chat/unknown-scope", cfg)
    assert r.ok is False
    assert r.reason == "unknown_scope"


def test_loop_guard_unchanged():
    assert should_send_to_source(MessageOrigin.AMO_MANAGER_OUTBOUND) is True
    assert should_send_to_source(MessageOrigin.AMO_IMPORTED_MIRROR) is False
    assert should_send_to_source(MessageOrigin.SOURCE_NATIVE_OUTBOUND) is False
