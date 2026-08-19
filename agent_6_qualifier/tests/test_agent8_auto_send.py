"""Tests for owner outreach channel helpers (canonical Agent 7 Envoy paths)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import re

from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits
from agent7_envoy.messaging.facebook_messenger import FacebookMessengerOwnerTransport

_LISTING_RE = re.compile(r"/marketplace/item/(\d+)")


def _extract_listing_id(url: str) -> str:
    m = _LISTING_RE.search(url or "")
    return m.group(1) if m else ""


def test_normalize_phone_thailand():
    assert normalize_phone_e164_digits("+66 81 234 5678") == "66812345678"


def test_extract_listing_id_from_marketplace_url():
    url = "https://www.facebook.com/marketplace/item/1515735863510647"
    assert _extract_listing_id(url) == "1515735863510647"


def test_fb_transport_resolves_profile(monkeypatch):
    monkeypatch.delenv("AGENT7_FACEBOOK_PROFILE_DIR", raising=False)
    t = FacebookMessengerOwnerTransport()
    assert t.profile_dir is not None
    assert t.profile_dir.name == "facebook_agent7"
