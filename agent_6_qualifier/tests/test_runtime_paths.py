"""Env overrides for production-persistent paths (defaults stay app-relative)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.runtime_paths import (  # noqa: E402
    amo_task_state_path,
    contracts_dir,
    owners_path,
    qualifier_session_store_dir,
    telethon_session_dir,
)
from agent6_qualifier.tg_account import qualifier_root, session_file_path, session_name  # noqa: E402


def test_defaults_are_app_relative(monkeypatch):
    for key in (
        "TG_SESSION_DIR",
        "OPENHOME_SESSIONS_DIR",
        "AGENT6_SESSION_STORE_DIR",
        "AGENT8_CONTRACTS_DIR",
        "OPENHOME_CONTRACTS_DIR",
        "AGENT7_OWNERS_PATH",
        "AMO_TASK_STATE_PATH",
        "TG_SESSION",
    ):
        monkeypatch.delenv(key, raising=False)
    root = qualifier_root()
    assert telethon_session_dir() == root
    assert session_name() == "agent7_userbot"
    assert session_file_path() == root / "agent7_userbot.session"
    assert qualifier_session_store_dir() == root / "data" / "sessions"
    assert contracts_dir() == root / "data" / "contracts"
    assert owners_path() == root / "data" / "owners.json"
    assert amo_task_state_path() == root / "data" / "amo_task_state.json"


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("TG_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TG_SESSION", "official_company")
    monkeypatch.setenv("AGENT6_SESSION_STORE_DIR", str(tmp_path / "qsess"))
    monkeypatch.setenv("AGENT8_CONTRACTS_DIR", str(tmp_path / "contracts"))
    monkeypatch.setenv("AGENT7_OWNERS_PATH", str(tmp_path / "owners.json"))
    monkeypatch.setenv("AMO_TASK_STATE_PATH", str(tmp_path / "amo.json"))
    assert session_file_path() == tmp_path / "sessions" / "official_company.session"
    assert qualifier_session_store_dir() == tmp_path / "qsess"
    assert contracts_dir() == tmp_path / "contracts"
    assert owners_path() == tmp_path / "owners.json"
    assert amo_task_state_path() == tmp_path / "amo.json"
