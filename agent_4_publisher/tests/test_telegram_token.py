#!/usr/bin/env python3
"""Publisher Telegram showcase must use the namespaced publisher bot token."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish_telegram as tg  # noqa: E402


def test_telegram_token_prefers_publisher_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_BOT_TOKEN_PUBLISHER", "publisher-bot-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fb-parser-bot-token")
    assert tg.telegram_token() == "publisher-bot-token"


def test_telegram_token_falls_back_to_legacy_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_BOT_TOKEN_PUBLISHER", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy-publisher-token")
    assert tg.telegram_token() == "legacy-publisher-token"


def test_telegram_token_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_BOT_TOKEN_PUBLISHER", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="TG_BOT_TOKEN_PUBLISHER"):
        tg.telegram_token()


def test_telegram_channel_prefers_env_then_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@FromEnv")
    assert tg.telegram_channel({}) == "@FromEnv"
    monkeypatch.delenv("TELEGRAM_CHANNEL", raising=False)
    assert tg.telegram_channel({"telegram": {"channel": "@FromConfig"}}) == "@FromConfig"
    assert tg.telegram_channel({}) == "@OpenHome_th"


def test_check_channel_access_rejects_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_BOT_TOKEN_PUBLISHER", "publisher-bot-token")
    monkeypatch.delenv("TELEGRAM_CHANNEL", raising=False)
    monkeypatch.setattr(
        tg,
        "check_bot",
        lambda: {"id": 1, "username": "fb_parser_mrkt_bot"},
    )
    monkeypatch.setattr(
        tg,
        "_tg_get",
        lambda method, params: {
            "ok": True,
            "result": {"status": "left"},
        },
    )
    with pytest.raises(RuntimeError, match="не админ канала"):
        tg.check_channel_access({"telegram": {"channel": "@OpenHome_th"}})


def test_check_channel_access_accepts_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_BOT_TOKEN_PUBLISHER", "publisher-bot-token")
    monkeypatch.delenv("TELEGRAM_CHANNEL", raising=False)
    monkeypatch.setattr(
        tg,
        "check_bot",
        lambda: {"id": 42, "username": "trip_home_phuket_bot"},
    )
    monkeypatch.setattr(
        tg,
        "_tg_get",
        lambda method, params: {
            "ok": True,
            "result": {"status": "administrator"},
        },
    )
    info = tg.check_channel_access({"telegram": {"channel": "@OpenHome_th"}})
    assert info["can_post"] is True
    assert info["bot_username"] == "trip_home_phuket_bot"
    assert info["channel"] == "@OpenHome_th"
