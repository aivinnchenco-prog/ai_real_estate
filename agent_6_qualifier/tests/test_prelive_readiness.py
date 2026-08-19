"""Offline tests for pre-live readiness (DNS waiting, gates, registration helpers)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7_envoy.amo_chat.channel_config import (
    channel_presence,
    format_channel_presence_report,
    validate_channel_config,
)
from agent7_envoy.amo_chat.config import AmoChatChannelConfig, AmoChatConfig
from agent7_envoy.amo_chat.dns_readiness import parse_dns_answers
from agent7_envoy.amo_chat.events import amo_chat_event
from agent7_envoy.amo_chat.mirror_store import AmoChatMirrorRecord, AmoChatMirrorStore
from agent7_envoy.amo_chat.signing import sign_request
from agent7_envoy.amo_chat.webhook import AmoChatWebhookHandler
from agent7_envoy.live_gates import (
    LIVE_GATE_SPECS,
    any_live_enabled,
    format_live_gate_matrix,
    read_live_gates,
    source_native_send_allowed,
)
from agent7_envoy.amo_chat.registration import (
    FACEBOOK_CHANNEL_CODE,
    AIRBNB_CHANNEL_CODE,
)
from agent7_envoy.amo_chat.production import production_registration_webhook_url as prod_url


def _cfg(tmp_path, *, secret="sec-fb", bot="bot-fb", scope="scope-fb"):
    return AmoChatConfig(
        amojo_base_url="https://amojo.amocrm.ru",
        account_id="acct",
        owner_silent_default=True,
        webhook_enabled=True,
        airbnb_enabled=True,
        facebook=AmoChatChannelConfig(
            key="facebook",
            title="Open Home | Facebook Marketplace",
            channel_id="ch-fb",
            channel_secret=secret,
            scope_id=scope,
            bot_id=bot,
            account_id="acct",
            webhook_enabled=True,
        ),
        airbnb=AmoChatChannelConfig(
            key="airbnb",
            title="Open Home | Airbnb",
            channel_id="",
            channel_secret="",
            scope_id="",
            bot_id="",
            account_id="acct",
            webhook_enabled=True,
        ),
        state_path=tmp_path / "state.json",
    )


def test_live_gate_matrix_defaults_off(monkeypatch):
    for name, default, _ in LIVE_GATE_SPECS:
        monkeypatch.delenv(name, raising=False)
    gates = read_live_gates()
    assert all(g.safe for g in gates)
    assert any_live_enabled(gates) is False
    text = format_live_gate_matrix(gates)
    assert "OVERALL SAFE: YES" in text
    assert "AGENT7_LIVE_OUTREACH_ENABLED: OFF" in text


def test_source_native_requires_dual_gates(monkeypatch):
    monkeypatch.setenv("AGENT7_LIVE_OUTREACH_ENABLED", "true")
    monkeypatch.setenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", "false")
    assert source_native_send_allowed("facebook") is False
    monkeypatch.setenv("AGENT7_FACEBOOK_MESSENGER_ENABLED", "true")
    assert source_native_send_allowed("facebook") is True


def test_dns_waiting_and_ready_parsers():
    waiting = parse_dns_answers([])
    assert waiting.status == "WAITING"
    ready = parse_dns_answers(["72.60.108.152"])
    assert ready.ready is True
    mismatch = parse_dns_answers(["1.2.3.4"])
    assert mismatch.status == "MISMATCH"


def test_ssl_script_refuses_nxdomain():
    script = (ROOT / "deploy" / "finish_https.sh").read_text(encoding="utf-8")
    assert "STOP: DNS not ready" in script
    assert "certbot --nginx" in script
    assert "dig +short" in script


def test_registration_data_codes():
    assert FACEBOOK_CHANNEL_CODE == "OpenHomeFacebook"
    assert AIRBNB_CHANNEL_CODE == "OpenHomeAirbnb"
    assert prod_url() == "https://api.open-home.online/webhooks/amo-chat/:scope_id"
    assert (ROOT / "deploy" / "amo_chat_registration_request.md").exists()
    assert (ROOT / "deploy" / "icons" / "openhome_facebook_channel.svg").exists()
    assert (ROOT / "deploy" / "icons" / "openhome_airbnb_channel.svg").exists()


def test_channel_config_missing_secret_and_bot(tmp_path):
    cfg = _cfg(tmp_path, secret="", bot="")
    fb = channel_presence(cfg.facebook)
    assert fb.channel_id is True
    assert fb.channel_secret is False
    assert fb.bot_id is False
    report = format_channel_presence_report(cfg)
    assert "secret MISSING" in report
    assert "bot_id MISSING" in report
    assert "AIRBNB:" in report
    assert validate_channel_config(cfg)["airbnb"].credentials_ready is False


def test_missing_scope_id_not_connected(tmp_path):
    cfg = _cfg(tmp_path, scope="")
    assert channel_presence(cfg.facebook).connected is False


def test_duplicate_webhook_send_once(tmp_path):
    cfg = _cfg(tmp_path)
    store = AmoChatMirrorStore(path=tmp_path / "m.json")
    store.upsert(
        AmoChatMirrorRecord(
            channel="facebook",
            owner_request_id="orq-1",
            conversation_id="conv-1",
            external_thread_id="thread-1",
            object_id="F_1",
        )
    )
    sent = []

    def send_fn(**kw):
        sent.append(kw)
        return type("R", (), {"outcome": "SENT"})()

    handler = AmoChatWebhookHandler(cfg, store=store, dry_run=False, send_to_source=send_fn)
    body = {
        "new_message": {
            "conversation_id": "conv-1",
            "msgid": "mgr-1",
            "text": "hello",
            "sender": {"id": "manager", "name": "Mgr"},
            "receiver": {"id": "owner"},
        }
    }
    raw = json.dumps(body).encode()
    headers = sign_request(
        method="POST", body=raw, path="/webhooks/amo-chat/facebook", secret="sec-fb"
    )
    r1 = handler.handle(
        channel_key="facebook", body=raw, headers=headers, path="/webhooks/amo-chat/facebook"
    )
    r2 = handler.handle(
        channel_key="facebook", body=raw, headers=headers, path="/webhooks/amo-chat/facebook"
    )
    assert r1.action == "sent"
    assert r2.action == "ignored"
    assert len(sent) == 1


def test_event_log_redacts_secret_fields(capsys):
    amo_chat_event(
        "AMO_CHAT_WEBHOOK_RECEIVED",
        channel="facebook",
        channel_secret="SHOULD_NOT_APPEAR",
        text="raw owner message",
        msgid="m1",
    )
    err = capsys.readouterr().err
    assert "SHOULD_NOT_APPEAR" not in err
    assert "raw owner message" not in err
    assert "msgid=m1" in err


def test_rollback_and_recovery_docs_exist():
    assert (ROOT / "deploy" / "ROLLBACK.md").exists()
    assert (ROOT / "deploy" / "RECOVERY.md").exists()
    assert (ROOT / "deploy" / "AMO_CHAT_DRY_RUN_PLAN.md").exists()
    assert (ROOT / "deploy" / "POST_SSL_SEQUENCE.md").exists()
    text = (ROOT / "deploy" / "ROLLBACK.md").read_text(encoding="utf-8")
    assert "AMO_CHAT_MIRROR_LIVE=false" in text
    assert "do not delete" in text.lower() or "without deleting" in text.lower()


def test_systemd_and_nginx_hardening_markers():
    unit = (ROOT / "deploy" / "systemd" / "openhome-api.service").read_text(encoding="utf-8")
    assert "TimeoutStopSec=30" in unit
    assert "User=REPLACE_USER" in unit or "User=openhome" in unit
    assert "127.0.0.1" in unit
    conf = (ROOT / "deploy" / "nginx" / "api.open-home.online.conf").read_text(encoding="utf-8")
    assert "server_tokens off" in conf
    assert "client_max_body_size 2m" in conf
    assert "autoindex off" in conf
    assert "ssl_certificate" not in conf
