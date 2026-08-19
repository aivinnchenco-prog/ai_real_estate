"""Normalize social publication URLs for exact matching (no network by default)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "fbclid", "gclid", "igsh", "igshid", "mibextid", "si", "feature",
    "ref", "ref_src", "ref_url", "share_id",
})

_SHORT_HOSTS = frozenset({
    "vm.tiktok.com", "vt.tiktok.com", "t.co", "bit.ly", "fb.watch",
})


@dataclass(frozen=True)
class NormalizedPublicationUrl:
    platform: str
    canonical_url: str
    publication_ref: str | None = None
    unresolved_short: bool = False


UrlExpander = Callable[[str], str | None]


def extract_urls_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(").,;]")
        if url not in seen:
            seen.append(url)
    return seen


def _strip_tracking(query: str) -> str:
    if not query:
        return ""
    pairs = []
    for part in query.split("&"):
        if not part:
            continue
        key = part.split("=", 1)[0].lower()
        if key in _TRACKING_PARAMS:
            continue
        pairs.append(part)
    return "&".join(pairs)


def _canonical_host(host: str) -> str:
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "twitter.com":
        return "x.com"
    if host == "m.facebook.com":
        return "facebook.com"
    return host


def _path_only(url: str) -> str:
    parsed = urlparse(url)
    host = _canonical_host(parsed.netloc)
    path = re.sub(r"/+$", "", parsed.path or "")
    query = _strip_tracking(parsed.query)
    return urlunparse((parsed.scheme or "https", host, path, "", query, ""))


def normalize_publication_url(
    url: str,
    *,
    expander: UrlExpander | None = None,
) -> NormalizedPublicationUrl | None:
    raw = (url or "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw.lstrip("/")

    host = _canonical_host(urlparse(raw).netloc)
    if host in _SHORT_HOSTS:
        if expander is None:
            return NormalizedPublicationUrl(
                platform="unknown",
                canonical_url=raw,
                unresolved_short=True,
            )
        expanded = expander(raw)
        if not expanded:
            return NormalizedPublicationUrl(
                platform="unknown",
                canonical_url=raw,
                unresolved_short=True,
            )
        raw = expanded
        host = _canonical_host(urlparse(raw).netloc)

    parsed = urlparse(raw)
    path = parsed.path or ""

    # Instagram
    if host.endswith("instagram.com"):
        m = re.search(r"/(p|reel|reels|tv)/([^/?#]+)", path, re.I)
        if m:
            kind = "reel" if m.group(1).lower() in {"reel", "reels"} else m.group(1).lower()
            ref = m.group(2)
            canonical = f"https://instagram.com/{kind}/{ref}"
            platform = "instagram_reel" if kind == "reel" else "instagram_carousel"
            return NormalizedPublicationUrl(platform, canonical, ref)
        return None

    # Facebook / fb.watch
    if host in {"facebook.com", "fb.com", "fb.watch"}:
        if host == "fb.watch":
            ref = path.strip("/").split("/")[0] if path.strip("/") else None
            canonical = f"https://fb.watch/{ref}" if ref else raw
            return NormalizedPublicationUrl("facebook", canonical, ref)
        m = re.search(r"/reel/(\d+)", path)
        if m:
            ref = m.group(1)
            return NormalizedPublicationUrl("facebook", f"https://facebook.com/reel/{ref}", ref)
        m = re.search(r"/groups/\d+/posts/(\d+)", path)
        if m:
            ref = m.group(1)
            return NormalizedPublicationUrl("fb_groups", _path_only(raw), ref)
        m = re.search(r"/marketplace/item/(\d+)", path)
        if m:
            ref = m.group(1)
            return NormalizedPublicationUrl("fb_marketplace", _path_only(raw), ref)
        m = re.search(r"/posts/(\d+)", path)
        if m:
            return NormalizedPublicationUrl("facebook", _path_only(raw), m.group(1))
        if "/share/" in path:
            return NormalizedPublicationUrl("facebook", _path_only(raw), None)
        return NormalizedPublicationUrl("facebook", _path_only(raw), None)

    # TikTok
    if host.endswith("tiktok.com"):
        m = re.search(r"/@([^/]+)/video/(\d+)", path)
        if m:
            ref = m.group(2)
            return NormalizedPublicationUrl("tiktok", f"https://tiktok.com/@{m.group(1)}/video/{ref}", ref)
        return NormalizedPublicationUrl("tiktok", _path_only(raw), None)

    # YouTube
    if host in {"youtube.com", "youtu.be", "m.youtube.com"}:
        if host == "youtu.be":
            ref = path.strip("/").split("/")[0]
            return NormalizedPublicationUrl("youtube", f"https://youtube.com/watch?v={ref}", ref)
        if "/shorts/" in path:
            ref = path.split("/shorts/")[1].split("/")[0]
            return NormalizedPublicationUrl("youtube", f"https://youtube.com/shorts/{ref}", ref)
        qs = parse_qs(parsed.query)
        if qs.get("v"):
            ref = qs["v"][0]
            return NormalizedPublicationUrl("youtube", f"https://youtube.com/watch?v={ref}", ref)
        return None

    # Telegram
    if host in {"t.me", "telegram.me"}:
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2 and parts[1].isdigit():
            channel, msg_id = parts[0], parts[1]
            canonical = f"https://t.me/{channel}/{msg_id}"
            return NormalizedPublicationUrl("telegram", canonical, msg_id)
        if len(parts) >= 3 and parts[0] == "c" and parts[-1].isdigit():
            canonical = f"https://t.me/c/{parts[1]}/{parts[-1]}"
            return NormalizedPublicationUrl("telegram", canonical, parts[-1])
        return None

    # X / Twitter
    if host in {"x.com", "twitter.com"}:
        m = re.search(r"/status/(\d+)", path)
        if m:
            ref = m.group(1)
            return NormalizedPublicationUrl("x", f"https://x.com{path.split('/status/')[0]}/status/{ref}", ref)
        return None

    # LinkedIn
    if host.endswith("linkedin.com"):
        if any(x in path for x in ("/posts/", "/feed/update/", "/urn:li:")):
            return NormalizedPublicationUrl("linkedin", _path_only(raw), None)
        return None

    # Threads
    if host.endswith("threads.net"):
        m = re.search(r"/post/([^/?#]+)", path)
        if m:
            return NormalizedPublicationUrl("threads", f"https://threads.net/post/{m.group(1)}", m.group(1))
        return None

    # PostMyPost planner/preview
    if host.endswith("postmypost.io"):
        m = re.search(r"/publications/(\d+)", path)
        if m:
            ref = m.group(1)
            return NormalizedPublicationUrl("postmypost", f"https://app.postmypost.io/publications/{ref}", ref)
        return None

    return None


def telegram_forward_url(channel: str | None, message_id: int | str | None) -> str | None:
    if not channel or message_id is None:
        return None
    channel = str(channel).lstrip("@")
    return f"https://t.me/{channel}/{int(message_id)}"
