"""Agent 9 Facebook profile path tests."""

from __future__ import annotations

from pathlib import Path

from agent9_connector.config_loader import (
    DEFAULT_AGENT9_FB_PROFILE_DIR,
    facebook_profile_dir,
    legacy_facebook_profile_dir,
)
from agent9_connector.connectors.facebook import FacebookConnector
from agent9_connector.profile_lock import lock_path


def test_default_profile_path():
    path = facebook_profile_dir()
    assert path == Path(DEFAULT_AGENT9_FB_PROFILE_DIR)
    assert path.name == "facebook_agent9"


def test_connector_uses_config_profile():
    fb = FacebookConnector()
    assert fb.profile_path() == facebook_profile_dir()


def test_legacy_profile_preserved_separately(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT9_DATA_DIR", str(tmp_path))
    legacy = legacy_facebook_profile_dir()
    assert legacy == tmp_path / "facebook_profile"
    assert legacy != facebook_profile_dir()


def test_lock_name_matches_profile():
    assert lock_path().name == "facebook_agent9.lock"
