"""Agent7 Facebook profile resolves to shared Marketplace parser session."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7_envoy.messaging.facebook_messenger import FacebookMessengerOwnerTransport
from agent7_envoy.messaging.fb_profile import (
    default_parser_fb_profile_dir,
    resolve_agent7_facebook_profile_dir,
)


def test_default_profile_is_parser_fb_profile():
    expected = default_parser_fb_profile_dir()
    assert expected.name == ".fb_profile"
    assert "fb_parser" in str(expected)
    assert resolve_agent7_facebook_profile_dir() == expected


def test_transport_uses_parser_profile_by_default(monkeypatch):
    monkeypatch.delenv("AGENT7_FACEBOOK_PROFILE_DIR", raising=False)
    t = FacebookMessengerOwnerTransport()
    assert t.profile_dir == default_parser_fb_profile_dir()


def test_explicit_override_wins(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fb"
    custom.mkdir()
    monkeypatch.setenv("AGENT7_FACEBOOK_PROFILE_DIR", str(custom))
    assert resolve_agent7_facebook_profile_dir() == custom.resolve()
    t = FacebookMessengerOwnerTransport()
    assert t.profile_dir == custom.resolve()
