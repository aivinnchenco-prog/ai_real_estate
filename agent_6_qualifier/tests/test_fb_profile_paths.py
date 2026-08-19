"""Agent 7 / Agent 9 Facebook profile path isolation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Агент 9" / "src"))

from agent7.profile_lock import DEFAULT_AGENT7_FB_PROFILE_DIR, facebook_profile_dir, lock_path
from openhome_shared.facebook_profile_lock import resolve_profile_lock_path


OWNER_OUTREACH = Path("/opt/openhome/runtime/browser_profiles/facebook_owner_outreach")


def test_agent7_profile_default_path():
    path = facebook_profile_dir()
    assert path == Path(DEFAULT_AGENT7_FB_PROFILE_DIR)
    assert path.name == "facebook_agent7"


def test_agent7_no_owner_outreach_fallback(monkeypatch):
    monkeypatch.delenv("AGENT7_FACEBOOK_PROFILE_DIR", raising=False)
    monkeypatch.setenv("FB_BROWSER_PROFILE", str(OWNER_OUTREACH))
    path = facebook_profile_dir()
    assert path.name == "facebook_agent7"
    assert path != OWNER_OUTREACH.resolve()


def test_agent7_lock_matches_profile_name():
    lock = lock_path()
    assert lock.name == "facebook_agent7.lock"


def test_profiles_do_not_overlap():
    from agent9_connector.config_loader import (
        DEFAULT_AGENT9_FB_PROFILE_DIR,
        facebook_profile_dir as agent9_profile_dir,
        legacy_facebook_profile_dir,
    )

    a7 = facebook_profile_dir()
    a9 = agent9_profile_dir()
    legacy = legacy_facebook_profile_dir()
    assert a7 != a9
    assert a7 != OWNER_OUTREACH
    assert a9 == Path(DEFAULT_AGENT9_FB_PROFILE_DIR)
    assert a9.name == "facebook_agent9"
    assert legacy.name == "facebook_profile"
    assert resolve_profile_lock_path(a7) != resolve_profile_lock_path(a9)
    assert resolve_profile_lock_path(OWNER_OUTREACH).name == "facebook_owner_outreach.lock"
