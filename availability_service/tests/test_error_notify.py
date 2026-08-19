"""Tests for availability error_notify (Telegram dedup)."""
from __future__ import annotations

import json
from unittest.mock import patch

from availability_service.app.error_notify import (
    FACEBOOK_SESSION_LOST_TAG,
    _state_path,
    maybe_notify_facebook_session_lost,
    maybe_notify_facebook_session_restored,
    notify,
)


def test_notify_skips_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("ERROR_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ERROR_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "availability_service.app.error_notify.resolve_runtime_dir",
        lambda: tmp_path,
    )
    assert notify("test", tag="t1") is False


def test_facebook_session_lost_and_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "availability_service.app.error_notify.resolve_runtime_dir",
        lambda: tmp_path,
    )
    monkeypatch.setenv("ERROR_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("ERROR_CHAT_ID", "12345")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = None
        sent_lost = maybe_notify_facebook_session_lost("redirect_or_login_form")
        assert sent_lost is True

        state = json.loads(_state_path().read_text(encoding="utf-8"))
        assert state.get("facebook_session_lost") is True

        sent_restore = maybe_notify_facebook_session_restored()
        assert sent_restore is True
        assert mock_urlopen.call_count == 2

        state = json.loads(_state_path().read_text(encoding="utf-8"))
        assert "facebook_session_lost" not in state


def test_facebook_session_lost_dedup_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "availability_service.app.error_notify.resolve_runtime_dir",
        lambda: tmp_path,
    )
    monkeypatch.setenv("ERROR_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("ERROR_CHAT_ID", "12345")
    monkeypatch.setenv("ERROR_NOTIFY_COOLDOWN_SEC", "3600")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = None
        assert maybe_notify_facebook_session_lost("first") is True
        assert maybe_notify_facebook_session_lost("second") is False
        assert mock_urlopen.call_count == 1

        state = json.loads(_state_path().read_text(encoding="utf-8"))
        assert FACEBOOK_SESSION_LOST_TAG in (state.get("tag_times") or {})
