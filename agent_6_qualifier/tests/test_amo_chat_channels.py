"""Offline tests for amoCRM Chat API custom owner channels."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7_envoy.amo_chat.client import AmojoChatClient
from agent7_envoy.amo_chat.config import (
    AmoChatChannelConfig,
    AmoChatConfig,
    save_channel_scope,
)
from agent7_envoy.amo_chat.identity import (
    stable_conversation_id,
    stable_message_id,
)
from agent7_envoy.amo_chat.mirror import AmoChatMirrorService
from agent7_envoy.amo_chat.mirror_store import AmoChatMirrorStore
from agent7_envoy.amo_chat.origin import (
    MessageOrigin,
    classify_amo_webhook_message,
    should_mirror_to_amo,
    should_send_to_source,
)
from agent7_envoy.amo_chat.signing import content_md5, sign_request, verify_signature
from agent7_envoy.amo_chat.webhook import AmoChatWebhookHandler
from agent7_envoy.crm_visibility import CrmVisibilityMode, resolve_owner_channel_crm_policy


def _cfg(tmp_path, *, fb_scope="", ab_scope="", secret="sec-fb", secret_ab="sec-ab"):
    return AmoChatConfig(
        amojo_base_url="https://amojo.amocrm.ru",
        account_id="acct-1",
        owner_silent_default=True,
        webhook_enabled=True,
        facebook=AmoChatChannelConfig(
            key="facebook",
            title="Open Home | Facebook Marketplace",
            channel_id="ch-fb",
            channel_secret=secret,
            scope_id=fb_scope,
            bot_id="bot-fb",
            account_id="acct-1",
            webhook_enabled=True,
        ),
        airbnb=AmoChatChannelConfig(
            key="airbnb",
            title="Open Home | Airbnb",
            channel_id="ch-ab",
            channel_secret=secret_ab,
            scope_id=ab_scope,
            bot_id="bot-ab",
            account_id="acct-1",
            webhook_enabled=True,
        ),
        state_path=tmp_path / "state.json",
    )


# ---- signing ----

def test_content_md5_and_signature_stable():
    body = b'{"account_id":"a","title":"t","hook_api_version":"v2"}'
    md5 = content_md5(body)
    assert md5 == content_md5(body)
    assert md5 == md5.lower()
    headers = sign_request(
        method="POST",
        body=body,
        path="/v2/origin/custom/x/connect",
        secret="secret",
        date="Thu, 01 Jan 2026 12:00:00 +0000",
    )
    assert headers["Content-MD5"] == md5
    assert headers["X-Signature"] == headers["X-Signature"].lower()
    assert verify_signature(
        method="POST",
        body=body,
        path="/v2/origin/custom/x/connect",
        secret="secret",
        headers=headers,
    )
    assert not verify_signature(
        method="POST",
        body=body,
        path="/v2/origin/custom/x/connect",
        secret="wrong",
        headers=headers,
    )


def test_bad_secret_rejected():
    body = b"{}"
    headers = sign_request(method="GET", body=body, path="/v2/x", secret="a")
    assert not verify_signature(
        method="GET", body=body, path="/v2/x", secret="b", headers=headers
    )


# ---- channels / policy ----

def test_fb_airbnb_independent_scopes(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="scope-fb", ab_scope="scope-ab")
    assert cfg.facebook.scope_id != cfg.airbnb.scope_id
    assert cfg.facebook.title != cfg.airbnb.title


def test_missing_custom_channel_falls_back_business_only(monkeypatch):
    monkeypatch.delenv("AMO_CHAT_FB_CHANNEL_ID", raising=False)
    monkeypatch.delenv("AMO_CHAT_FB_CHANNEL_SECRET", raising=False)
    monkeypatch.delenv("AMO_CHAT_FB_SCOPE_ID", raising=False)
    p = resolve_owner_channel_crm_policy("facebook_messenger")
    assert p.visibility == CrmVisibilityMode.BUSINESS_EVENTS_ONLY


def test_configured_custom_channel_policy(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, fb_scope="scope-fb", ab_scope="scope-ab")

    def fake_load():
        return cfg

    monkeypatch.setattr(
        "agent7_envoy.crm_visibility.load_amo_chat_config", fake_load, raising=False
    )
    monkeypatch.setattr(
        "agent7_envoy.amo_chat.config.load_amo_chat_config", fake_load
    )
    # Patch the import path used inside _amo_custom_ready
    import agent7_envoy.crm_visibility as cv

    monkeypatch.setattr(
        cv,
        "_amo_custom_ready",
        lambda key: key in {"facebook", "airbnb"},
    )
    p = resolve_owner_channel_crm_policy("facebook_messenger")
    assert p.visibility == CrmVisibilityMode.CUSTOM_CHAT_MIRROR
    p2 = resolve_owner_channel_crm_policy("airbnb_messages")
    assert p2.visibility == CrmVisibilityMode.CUSTOM_CHAT_MIRROR


# ---- source → amo ----

def test_source_outbound_mirrored_once(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="scope-fb")
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(json.loads(body.decode()))
        return 200, b'{"ok":true}'

    client = AmojoChatClient(cfg, transport=transport, dry_run=False)
    store = AmoChatMirrorStore(path=tmp_path / "m.json")
    svc = AmoChatMirrorService(cfg, store=store, client=client, dry_run=False)
    r1 = svc.mirror_source_message(
        channel="facebook",
        owner_request_id="orq-1",
        object_id="F_1",
        text="hello owner",
        origin=MessageOrigin.SOURCE_NATIVE_OUTBOUND,
        external_thread_id="fb-t-1",
        external_message_id="ext-1",
    )
    r2 = svc.mirror_source_message(
        channel="facebook",
        owner_request_id="orq-1",
        object_id="F_1",
        text="hello owner",
        origin=MessageOrigin.SOURCE_NATIVE_OUTBOUND,
        external_thread_id="fb-t-1",
        external_message_id="ext-1",
    )
    assert r1.ok and not r1.skipped
    assert r2.skipped and r2.reason == "duplicate_msgid"
    assert len(calls) == 1
    assert calls[0]["payload"]["silent"] is True


def test_source_inbound_mirrored_once_airbnb(tmp_path):
    cfg = _cfg(tmp_path, ab_scope="scope-ab")
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(1)
        return 200, b"{}"

    client = AmojoChatClient(cfg, transport=transport, dry_run=False)
    store = AmoChatMirrorStore(path=tmp_path / "m.json")
    svc = AmoChatMirrorService(cfg, store=store, client=client, dry_run=False)
    kwargs = dict(
        channel="airbnb",
        owner_request_id="orq-a",
        object_id="A_1",
        text="host reply",
        origin=MessageOrigin.SOURCE_NATIVE_INBOUND,
        external_thread_id="ab-t-1",
        external_message_id="in-1",
    )
    assert svc.mirror_source_message(**kwargs).ok
    assert svc.mirror_source_message(**kwargs).skipped
    assert len(calls) == 1


def test_amo_down_degrades_not_blocks(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="scope-fb")

    def transport(method, url, headers, body, timeout):
        return 503, b"down"

    client = AmojoChatClient(cfg, transport=transport, dry_run=False)
    store = AmoChatMirrorStore(path=tmp_path / "m.json")
    svc = AmoChatMirrorService(cfg, store=store, client=client, dry_run=False)
    r = svc.mirror_source_message(
        channel="facebook",
        owner_request_id="orq-d",
        object_id="F_d",
        text="x",
        origin=MessageOrigin.SOURCE_NATIVE_OUTBOUND,
        external_thread_id="t",
        external_message_id="e1",
    )
    assert r.degraded is True
    rec = store.get_by_owner_request("orq-d")
    assert rec is not None
    assert rec.sync_state == "DEGRADED"


def test_not_configured_degraded_fallback(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="")  # not connected
    svc = AmoChatMirrorService(cfg, store=AmoChatMirrorStore(path=tmp_path / "m.json"), dry_run=True)
    r = svc.mirror_source_message(
        channel="facebook",
        owner_request_id="orq-x",
        object_id="F_x",
        text="x",
        origin=MessageOrigin.SOURCE_NATIVE_OUTBOUND,
    )
    assert r.degraded and r.skipped


# ---- loop prevention / amo → source ----

def test_imported_mirror_webhook_does_not_resend(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="scope-fb")
    store = AmoChatMirrorStore(path=tmp_path / "m.json")
    conv = stable_conversation_id(
        channel="facebook", object_id="F_1", external_thread_id="t1", owner_request_id="orq-1"
    )
    from agent7_envoy.amo_chat.mirror_store import AmoChatMirrorRecord

    store.upsert(
        AmoChatMirrorRecord(
            channel="facebook",
            owner_request_id="orq-1",
            conversation_id=conv,
            external_thread_id="t1",
            object_id="F_1",
            imported_msgids=["oh-facebook-outbound-ext-1"],
        )
    )
    sent = []
    handler = AmoChatWebhookHandler(
        cfg,
        store=store,
        dry_run=False,
        send_to_source=lambda **kw: sent.append(kw) or type("R", (), {"outcome": "SENT"})(),
    )
    body = {
        "new_message": {
            "conversation_id": conv,
            "msgid": "oh-facebook-outbound-ext-1",
            "text": "echo",
            "sender": {"id": "bot-fb", "name": "Open Home | Agent7"},
            "receiver": {"id": "owner"},
        }
    }
    # skip verify; craft signed headers
    raw = json.dumps(body).encode()
    headers = sign_request(
        method="POST",
        body=raw,
        path="/webhooks/amo-chat/facebook",
        secret="sec-fb",
    )
    r = handler.handle(
        channel_key="facebook",
        body=raw,
        headers=headers,
        path="/webhooks/amo-chat/facebook",
    )
    assert r.action == "ignored"
    assert sent == []


def test_manager_outbound_routes_to_exact_thread(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="scope-fb")
    store = AmoChatMirrorStore(path=tmp_path / "m.json")
    conv = stable_conversation_id(
        channel="facebook", object_id="F_1", external_thread_id="exact-thread", owner_request_id="orq-1"
    )
    from agent7_envoy.amo_chat.mirror_store import AmoChatMirrorRecord

    store.upsert(
        AmoChatMirrorRecord(
            channel="facebook",
            owner_request_id="orq-1",
            conversation_id=conv,
            external_thread_id="exact-thread",
            object_id="F_1",
            source_url="https://facebook.com/marketplace/item/1",
        )
    )
    sent = []

    def send_fn(**kw):
        sent.append(kw)
        return type("R", (), {"outcome": "SENT"})()

    handler = AmoChatWebhookHandler(cfg, store=store, dry_run=False, send_to_source=send_fn)
    body = {
        "new_message": {
            "conversation_id": conv,
            "msgid": "amo-mgr-1",
            "text": "manager hello",
            "sender": {"id": "user-amojo", "name": "Manager"},
            "receiver": {"id": "owner"},
        }
    }
    raw = json.dumps(body).encode()
    headers = sign_request(
        method="POST", body=raw, path="/webhooks/amo-chat/facebook", secret="sec-fb"
    )
    r = handler.handle(
        channel_key="facebook",
        body=raw,
        headers=headers,
        path="/webhooks/amo-chat/facebook",
    )
    assert r.action == "sent"
    assert sent[0]["external_thread_id"] == "exact-thread"
    assert store.get_by_conversation(conv).ownership == "HUMAN_ACTIVE"


def test_missing_thread_reports_failure(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="scope-fb")
    store = AmoChatMirrorStore(path=tmp_path / "m.json")
    handler = AmoChatWebhookHandler(
        cfg, store=store, dry_run=False, send_to_source=lambda **k: None
    )
    body = {
        "new_message": {
            "conversation_id": "unknown-conv",
            "msgid": "amo-mgr-2",
            "text": "x",
            "sender": {"id": "u", "name": "M"},
            "receiver": {"id": "o"},
        }
    }
    raw = json.dumps(body).encode()
    headers = sign_request(
        method="POST", body=raw, path="/webhooks/amo-chat/facebook", secret="sec-fb"
    )
    r = handler.handle(
        channel_key="facebook", body=raw, headers=headers, path="/webhooks/amo-chat/facebook"
    )
    assert r.reason == "EXTERNAL_THREAD_UNAVAILABLE"


def test_correlation_two_objects_do_not_mix():
    a = stable_conversation_id(
        channel="facebook", object_id="F_1", external_thread_id="t1", owner_request_id="orq1"
    )
    b = stable_conversation_id(
        channel="facebook", object_id="F_2", external_thread_id="t2", owner_request_id="orq2"
    )
    assert a != b
    fb = stable_conversation_id(
        channel="facebook", object_id="X", external_thread_id="same", owner_request_id="1"
    )
    ab = stable_conversation_id(
        channel="airbnb", object_id="X", external_thread_id="same", owner_request_id="1"
    )
    assert fb != ab


def test_origin_guards():
    assert should_send_to_source(MessageOrigin.AMO_MANAGER_OUTBOUND)
    assert not should_send_to_source(MessageOrigin.AMO_IMPORTED_MIRROR)
    assert should_mirror_to_amo(MessageOrigin.SOURCE_NATIVE_INBOUND)
    assert not should_mirror_to_amo(MessageOrigin.AMO_MANAGER_OUTBOUND)
    assert (
        classify_amo_webhook_message(
            msgid="oh-facebook-outbound-1",
            conversation_id="c",
            already_imported=False,
            sender_is_bot=False,
            has_receiver=True,
        )
        == MessageOrigin.AMO_IMPORTED_MIRROR
    )


def test_stable_msgid_idempotent():
    a = stable_message_id(
        channel="facebook",
        direction="outbound",
        owner_request_id="orq",
        external_message_id="ext-9",
    )
    b = stable_message_id(
        channel="facebook",
        direction="outbound",
        owner_request_id="orq",
        external_message_id="ext-9",
    )
    assert a == b


def test_save_scope_no_secret(tmp_path):
    path = save_channel_scope(
        channel_key="facebook",
        scope_id="scope-x",
        account_id="acct",
        path=tmp_path / "st.json",
    )
    data = json.loads(path.read_text())
    assert data["facebook"]["scope_id"] == "scope-x"
    assert "secret" not in json.dumps(data).lower() or "channel_secret" not in data.get(
        "facebook", {}
    )


def test_unwanted_lead_protection_default_silent(tmp_path):
    cfg = _cfg(tmp_path, fb_scope="scope-fb")
    assert cfg.owner_silent_default is True
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(json.loads(body.decode()))
        return 200, b"{}"

    client = AmojoChatClient(cfg, transport=transport, dry_run=False)
    svc = AmoChatMirrorService(
        cfg, store=AmoChatMirrorStore(path=tmp_path / "m.json"), client=client, dry_run=False
    )
    svc.mirror_source_message(
        channel="facebook",
        owner_request_id="orq",
        object_id="F",
        text="hi",
        origin=MessageOrigin.SOURCE_NATIVE_INBOUND,
        external_thread_id="t",
        external_message_id="m1",
    )
    assert calls[0]["payload"]["silent"] is True
