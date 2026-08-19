"""Tests for shared Facebook profile lock paths."""

from __future__ import annotations

from pathlib import Path

from openhome_shared.facebook_profile_lock import resolve_profile_lock_path


def test_lock_path_derived_from_profile_name(monkeypatch, tmp_path):
    profile = tmp_path / "facebook_owner_outreach"
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("OPENHOME_FB_LOCK_DIR", str(lock_dir))
    monkeypatch.delenv("OPENHOME_FB_PROFILE_LOCK", raising=False)
    assert resolve_profile_lock_path(profile) == lock_dir / "facebook_owner_outreach.lock"


def test_lock_path_explicit_override(monkeypatch, tmp_path):
    explicit = tmp_path / "custom.lock"
    monkeypatch.setenv("OPENHOME_FB_PROFILE_LOCK", str(explicit))
    assert resolve_profile_lock_path(tmp_path / "any_profile") == explicit
