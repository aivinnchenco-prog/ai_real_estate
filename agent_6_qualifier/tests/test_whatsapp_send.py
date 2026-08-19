"""Tests for WhatsApp send (Wazzup + Green API)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7.whatsapp_send import (
    normalize_wazzup_chat_id,
    send_whatsapp,
    wazzup_configured,
)


def test_normalize_wazzup_chat_id_thailand():
    assert normalize_wazzup_chat_id("+66 81 234 5678") == "66812345678"


def test_send_wazzup_mock(monkeypatch):
    monkeypatch.setenv("WA_PROVIDER", "wazzup")
    monkeypatch.setenv("WAZZUP_API_KEY", "secret")
    monkeypatch.setenv("WAZZUP_CHANNEL_ID", "ch-1")
    monkeypatch.setenv("WAZZUP_SEND_ENABLED", "true")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs.get("json")))
        return type("R", (), {"ok": True})()

    monkeypatch.setattr("agent7.whatsapp_send.requests.post", fake_post)
    ok, err = send_whatsapp("+66812345678", "hello")
    assert ok and not err
    assert len(calls) == 1
    assert calls[0][1]["chatId"] == "66812345678"
    assert calls[0][1]["chatType"] == "whatsapp"


def test_wazzup_configured():
    import os
    os.environ.pop("WAZZUP_API_KEY", None)
    assert not wazzup_configured()
