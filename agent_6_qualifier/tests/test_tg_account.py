"""Offline tests for tg_account helpers and diagnose script."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.tg_account import (  # noqa: E402
    identity_check,
    mask_phone,
    session_file_path,
    session_name,
)


def test_mask_phone_masks_digits():
    assert mask_phone("+66812345678").endswith("5678")
    assert "***" in mask_phone("+66812345678")
    assert mask_phone("") == "(not set)"


def test_session_name_default(monkeypatch):
    monkeypatch.delenv("TG_SESSION", raising=False)
    assert session_name() == "agent7_userbot"


def test_session_name_official(monkeypatch):
    monkeypatch.delenv("TG_SESSION_DIR", raising=False)
    monkeypatch.delenv("OPENHOME_SESSIONS_DIR", raising=False)
    monkeypatch.setenv("TG_SESSION", "official_company")
    assert session_name() == "official_company"
    assert session_file_path().name == "official_company.session"


def test_session_file_path_uses_tg_session_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TG_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("TG_SESSION", "official_company")
    assert session_file_path() == tmp_path / "official_company.session"


def test_identity_check_skip(monkeypatch):
    monkeypatch.delenv("TG_EXPECTED_USER_ID", raising=False)
    monkeypatch.delenv("TG_EXPECTED_USERNAME", raising=False)
    me = SimpleNamespace(id=1, username="corp")
    assert identity_check(me) == "SKIP"


def test_identity_check_ok(monkeypatch):
    monkeypatch.setenv("TG_EXPECTED_USER_ID", "42")
    monkeypatch.setenv("TG_EXPECTED_USERNAME", "corp_account")
    me = SimpleNamespace(id=42, username="corp_account")
    assert identity_check(me) == "OK"


def test_identity_check_fail(monkeypatch):
    monkeypatch.setenv("TG_EXPECTED_USER_ID", "99")
    me = SimpleNamespace(id=42, username="other")
    assert identity_check(me) == "FAIL"


def test_tg_diagnose_no_send(monkeypatch):
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "tg_diagnose.py"
    spec = importlib.util.spec_from_file_location("tg_diagnose", script)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "hash")
    monkeypatch.setenv("TG_SESSION", "official_company")

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_me = AsyncMock(return_value=SimpleNamespace(
        id=100, username="corp", phone="+66123456789",
    ))

    with patch("agent6_qualifier.tg_userbot.make_client", return_value=client):
        spec.loader.exec_module(mod)
        rc = asyncio.run(mod._run())

    assert rc == 0
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    client.send_message.assert_not_called()
