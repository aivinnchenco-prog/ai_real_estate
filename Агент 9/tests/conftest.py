"""Shared pytest fixtures for Agent 9 offline tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    import agent9_connector.config_loader as cfg
    import agent9_connector.state_store as st
    monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "data_dir", lambda: tmp_path)
    from agent9_connector.state_store import StateStore
    return StateStore(tmp_path)

@pytest.fixture
def sample_page():
    return {
        "id": "page-1",
        "properties": {
            "Объект ID": {"rich_text": [{"plain_text": "F_20260807_001"}]},
            "Тип жилья": {"select": {"name": "Вилла"}},
            "Источник объявления": {"url": "https://www.facebook.com/marketplace/item/123456789"},
            "post_url_FB_marketplace": {"url": None},
            "FB Outreach Status": {"select": {"name": ""}},
            "WhatsApp контакт": {"rich_text": []},
            "Владелец / Агент": {"rich_text": []},
        },
    }
