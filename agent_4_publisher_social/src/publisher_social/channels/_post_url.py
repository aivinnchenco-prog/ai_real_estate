from __future__ import annotations

from typing import Any

from .base import ChannelResult

# Legacy opt-in only. Production gets post URLs later through an API.
_DEFAULT_WAIT_SECONDS = {
    "youtube_shorts": 35.0,
    "tiktok": 25.0,
    "tiktok_carousel": 25.0,
    "instagram_reel": 60.0,
    "instagram_carousel": 90.0,
    "linkedin": 60.0,
    "twitter": 30.0,
    "fb_groups": 20.0,
}


def android_post_url_capture_enabled(android_cfg: dict[str, Any]) -> bool:
    """Phone UI must never capture post URLs unless explicitly re-enabled."""
    capture_cfg = android_cfg.get("post_url_capture") or {}
    return bool(capture_cfg.get("enabled", False))


def attach_post_url(
    d,
    result: ChannelResult,
    channel: str,
    android_cfg: dict[str, Any],
    *,
    confirm_post: bool,
    wait_seconds: float | None = None,
) -> ChannelResult:
    if not confirm_post or not result.ok or result.skipped:
        return result
    if not android_post_url_capture_enabled(android_cfg):
        result.post_url = None
        result.extra_urls = None
        result.publication_status = "accepted"
        result.note = (
            f"{(result.note or '').rstrip()}; "
            "post_url_capture=disabled; post_url_source=api"
        ).strip("; ")
        return result

    from ..android.post_link import capture_post_url

    delay = (
        float(wait_seconds)
        if wait_seconds is not None
        else float(_DEFAULT_WAIT_SECONDS.get(channel, 20.0))
    )
    url, extra = capture_post_url(
        d, channel, android_cfg, wait_seconds=delay
    )
    if url:
        result.post_url = url
        result.publication_status = "verified"
        result.note = f"{(result.note or '').rstrip()}; post_url={url}".strip("; ")
    else:
        result.publication_status = "submitted_unverified"
        result.note = (
            f"{(result.note or '').rstrip()}; post_url=not_captured_after_{int(delay)}s"
        ).strip("; ")
    if extra:
        result.extra_urls = extra
    return result
