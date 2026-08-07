#!/usr/bin/env python3
"""PostMyPost social permalink extraction and validation."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

PUBLICATION_STATUS_PUBLISHED = 1
POST_STATUS_PUBLISHED = 1

POSTMYPOST_PLANNER_RE = re.compile(
    r"^https?://(?:app\.)?postmypost\.io/publications/\d+/?(?:\?.*)?$",
    re.IGNORECASE,
)

NETWORK_HOSTS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "twitter": ("x.com", "twitter.com"),
    "linkedin": ("linkedin.com",),
    "facebook": ("facebook.com", "fb.watch", "fb.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "threads": ("threads.net", "threads.com"),
}

FACEBOOK_POST_MARKERS = (
    "/posts/",
    "story_fbid",
    "permalink.php",
    "photo.php",
    "/reel/",
    "/videos/",
    "fb.watch/",
)
FACEBOOK_NUMERIC_POST_RE = re.compile(
    r"^https?://(?:www\.|m\.)?facebook\.com/\d+/?(?:\?.*)?$",
    re.IGNORECASE,
)

BLOCKED_URL_PARTS = (
    "postmypost.io",
    "metricool.com",
    "cloudflare",
    "r2.dev",
    ".mp4",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
)


def is_postmypost_planner_url(url: str | None) -> bool:
    if not url:
        return False
    return bool(POSTMYPOST_PLANNER_RE.match(url.strip()))


def _canonical_host(host: str) -> str:
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "twitter.com":
        return "x.com"
    if host == "m.facebook.com":
        return "facebook.com"
    return host


def is_valid_social_post_url(url: str, network: str) -> bool:
    """True when url is a final social permalink for the given network."""
    if not url or not url.startswith("http"):
        return False
    if is_postmypost_planner_url(url):
        return False
    lowered = url.lower()
    if any(part in lowered for part in BLOCKED_URL_PARTS):
        return False

    host = _canonical_host(urlparse(url).netloc)
    hints = NETWORK_HOSTS.get(network, ())
    if hints and not any(h in host for h in hints):
        return False

    if network == "instagram" and "/explore/locations/" in lowered:
        return False

    if network == "facebook":
        if FACEBOOK_NUMERIC_POST_RE.match(url):
            return True
        return any(marker in lowered for marker in FACEBOOK_POST_MARKERS)

    return True


def decide_notion_url_update(
    current_url: str | None,
    new_url: str | None,
    network: str,
) -> tuple[bool, str | None, str]:
    """
    Returns (should_update, url_to_write, reason).
    reason: empty|noop_same|replace_planner|write_new|conflict|invalid_new|not_ready
    """
    if not new_url:
        return False, None, "not_ready"
    if not is_valid_social_post_url(new_url, network):
        return False, None, "invalid_new"

    current = (current_url or "").strip()
    if not current:
        return True, new_url, "write_new"
    if is_postmypost_planner_url(current):
        return True, new_url, "replace_planner"
    if current.rstrip("/") == new_url.rstrip("/"):
        return False, None, "noop_same"
    if is_valid_social_post_url(current, network):
        return False, None, "conflict"
    return True, new_url, "write_new"
