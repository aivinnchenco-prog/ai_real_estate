"""Offline tests for local E2E launcher helpers (no live send / publication)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

from local_e2e_lib import (  # noqa: E402
    apply_safe_flags,
    force_live_flags_off,
    montage_lock_held,
    parse_cloudflared_url,
    pending_social_jobs,
    redact_for_log,
    refuse_phone_social_start,
    upsert_env_keys,
)


def test_no_phone_social_startup_by_default(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "old.json").write_text('{"object_id":"A_1"}', encoding="utf-8")
    # default path: enable=false → no refuse
    refuse_phone_social_start(enable_phone_social=False, jobs_dir=jobs)


def test_stale_jobs_guard_refuses_phone_social(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "A_old.json").write_text('{"object_id":"A_old"}', encoding="utf-8")
    assert pending_social_jobs(jobs)
    with pytest.raises(SystemExit) as exc:
        refuse_phone_social_start(enable_phone_social=True, jobs_dir=jobs)
    assert "REFUSE START" in str(exc.value)
    assert "pending old jobs" in str(exc.value)


def test_meta_never_starts_in_up_parser(tmp_path, monkeypatch):
    # Importing up and invoking with --enable-meta must refuse.
    monkeypatch.setattr(sys, "argv", ["local_e2e_up.py", "--enable-meta", "--no-tunnel", "--no-chain", "--no-webhook"])
    import local_e2e_up as up

    with pytest.raises(SystemExit) as exc:
        up.main()
    assert "Meta Ads" in str(exc.value)


def test_phone_social_flag_refused(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "local_e2e_up.py",
            "--enable-phone-social",
            "--no-tunnel",
            "--no-chain",
            "--no-webhook",
        ],
    )
    import local_e2e_up as up

    with pytest.raises(SystemExit) as exc:
        up.main()
    assert "phone social" in str(exc.value).lower() or "REFUSE" in str(exc.value)


def test_cloudflared_url_parsing():
    sample = (
        "2026-08-10T01:00:00Z INF Thank you for trying Cloudflare Tunnel\n"
        "INF |  https://old-dead-tunnel.trycloudflare.com                                |\n"
        "INF Connection registered\n"
        "INF |  https://abc-def-123.trycloudflare.com                                |\n"
    )
    assert parse_cloudflared_url(sample) == "https://abc-def-123.trycloudflare.com"
    assert parse_cloudflared_url("no url here") is None


def test_wazzup_patch_mocked(monkeypatch, tmp_path):
    sys.path.insert(0, str(REPO / "agent_6_qualifier" / "src"))
    from agent6_qualifier.messaging.wazzup_client import WazzupClient
    from agent6_qualifier.messaging.wazzup_config import WazzupConfig

    calls: list[tuple[str, str, bytes | None]] = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, body))
        assert "Authorization" in headers
        assert b"test-secret-key" not in (body or b"")
        if method == "PATCH":
            assert body is not None
            payload = json.loads(body.decode("utf-8"))
            assert "webhooksUri" in payload
            assert "subscriptions" in payload
            assert payload["subscriptions"]["messagesAndStatuses"] is True
            return 200, b'{"webhooksUri":"https://x.trycloudflare.com/webhooks/wazzup"}'
        return 200, b'{"webhooksUri":"https://x.trycloudflare.com/webhooks/wazzup"}'

    cfg = WazzupConfig(api_key="test-secret-key", send_enabled=False)
    client = WazzupClient(cfg, transport=transport)
    client.set_webhooks_uri("https://x.trycloudflare.com/webhooks/wazzup")
    got = client.get_webhooks()
    assert calls[0][0] == "PATCH"
    assert "/v3/webhooks" in calls[0][1]
    assert calls[1][0] == "GET"
    assert got["webhooksUri"].endswith("/webhooks/wazzup")


def test_health_verification_helper(monkeypatch):
    from local_e2e_lib import http_ok

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true}'

    monkeypatch.setattr("local_e2e_lib.urlopen", lambda *a, **k: Resp())
    assert http_ok("http://127.0.0.1:8765/health") is True


def test_down_restores_live_flags(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "WAZZUP_SEND_ENABLED=true\n"
        "WAZZUP_AUTO_REPLY_ENABLED=true\n"
        "AGENT7_LIVE_OUTREACH_ENABLED=true\n",
        encoding="utf-8",
    )
    force_live_flags_off(env_path=env)
    text = env.read_text(encoding="utf-8")
    assert "WAZZUP_SEND_ENABLED=false" in text
    assert "WAZZUP_AUTO_REPLY_ENABLED=false" in text
    assert "AGENT7_LIVE_OUTREACH_ENABLED=false" in text


def test_safe_flags_defaults(tmp_path):
    env = tmp_path / ".env"
    env.write_text("WAZZUP_API_KEY=should-not-appear-in-redact-test\n", encoding="utf-8")
    apply_safe_flags(env_path=env)
    text = env.read_text(encoding="utf-8")
    assert "WAZZUP_SEND_ENABLED=false" in text
    assert "META_ADS_ENABLED=false" in text


def test_no_secret_logging():
    raw = "WAZZUP_API_KEY=super-secret-value Bearer abc.def.ghi"
    out = redact_for_log(raw)
    assert "super-secret-value" not in out
    assert "abc.def.ghi" not in out
    assert "***" in out


def test_montage_lock_text_not_held(tmp_path):
    lock = tmp_path / "montage.lock"
    lock.write_text("stale-object-id", encoding="utf-8")
    held, _ = montage_lock_held(lock)
    assert held is False


def test_duplicate_launcher_reuses_webhook(monkeypatch, tmp_path):
    import local_e2e_up as up
    from local_e2e_lib import RuntimeState

    state = RuntimeState(pids={"wazzup_webhook": 999999}, services={}, tunnel={})
    monkeypatch.setattr(up, "pid_alive", lambda pid: pid == 999999)
    monkeypatch.setattr(up, "http_ok", lambda url: True)
    up._start_webhook(state)
    assert state.services["wazzup_webhook"]["status"] == "reused"
    assert state.pids["wazzup_webhook"] == 999999
