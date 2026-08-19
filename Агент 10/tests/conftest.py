"""Pytest fixtures for Agent 10."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent10_marketer.config import Agent10Config, load_config
from agent10_marketer.models import ObjectSummary, PeerGroupAttributes


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def config(tmp_data_dir: Path) -> Agent10Config:
    root = Path(__file__).resolve().parents[1]
    return load_config(config_dir=root / "config", data_dir=tmp_data_dir)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def object_summary() -> ObjectSummary:
    return ObjectSummary(
        object_id="1847",
        page_id="page-1847",
        title="Test Condo",
        peer=PeerGroupAttributes(
            property_type="Condo",
            district="Bang Tao",
            rent_type="monthly",
            price_band="60k_100k",
            bedrooms=2,
        ),
    )
