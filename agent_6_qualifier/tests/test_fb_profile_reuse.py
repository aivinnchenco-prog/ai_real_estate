"""Agent7 Facebook profile defaults to isolated owner-outreach profile."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7_envoy.messaging.facebook_messenger import FacebookMessengerOwnerTransport
from agent7_envoy.messaging.fb_profile import (
    DEFAULT_AGENT7_FB_PROFILE_DIR,
    resolve_agent7_facebook_profile_dir,
)


def test_default_profile_is_agent7_isolated():
    expected = Path(DEFAULT_AGENT7_FB_PROFILE_DIR)
    assert expected.name == "facebook_agent7"
    assert resolve_agent7_facebook_profile_dir() == expected.resolve()


def test_transport_uses_default_profile_by_default(monkeypatch):
    monkeypatch.delenv("AGENT7_FACEBOOK_PROFILE_DIR", raising=False)
    t = FacebookMessengerOwnerTransport()
    assert t.profile_dir == Path(DEFAULT_AGENT7_FB_PROFILE_DIR).resolve()


def test_explicit_override_wins(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fb"
    custom.mkdir()
    monkeypatch.setenv("AGENT7_FACEBOOK_PROFILE_DIR", str(custom))
    assert resolve_agent7_facebook_profile_dir() == custom.resolve()
    t = FacebookMessengerOwnerTransport()
    assert t.profile_dir == custom.resolve()
