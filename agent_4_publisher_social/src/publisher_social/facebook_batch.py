"""Facebook phone batch: fb_groups → pause → fb_marketplace in one live job."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .channels.base import ChannelResult
from .models import PublishJob
from .state import is_channel_done, load_state

DEFAULT_FACEBOOK_BATCH: dict[str, Any] = {
    "enabled": True,
    "channels": ["fb_groups", "fb_marketplace"],
    "delay_between_channels_seconds": 25,
    "stop_on_failure": True,
}


def resolve_facebook_batch_config(config: dict[str, Any]) -> dict[str, Any] | None:
    """Return batch settings or None when facebook_batch.enabled is false."""
    raw = config.get("facebook_batch")
    if raw is None:
        return dict(DEFAULT_FACEBOOK_BATCH)
    if raw.get("enabled") is False:
        return None
    return {
        "enabled": True,
        "channels": list(raw.get("channels") or DEFAULT_FACEBOOK_BATCH["channels"]),
        "delay_between_channels_seconds": int(
            raw.get("delay_between_channels_seconds")
            or DEFAULT_FACEBOOK_BATCH["delay_between_channels_seconds"]
        ),
        "stop_on_failure": bool(
            raw.get("stop_on_failure", DEFAULT_FACEBOOK_BATCH["stop_on_failure"])
        ),
    }


def facebook_batch_applies(config: dict[str, Any], channels: list[str]) -> bool:
    batch_cfg = resolve_facebook_batch_config(config)
    if not batch_cfg or not channels:
        return False
    batch_set = set(batch_cfg["channels"])
    return set(channels).issubset(batch_set) and bool(batch_set.intersection(channels))


def order_facebook_batch_channels(
    requested: list[str],
    batch_cfg: dict[str, Any],
) -> list[str]:
    batch_order = list(batch_cfg["channels"])
    return [ch for ch in batch_order if ch in requested]


def summarize_facebook_batch_status(results: list[ChannelResult]) -> str:
    if not results:
        return "nothing_to_publish"
    if all(r.skipped for r in results):
        return "nothing_to_publish"
    done = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok and not r.skipped]
    if failed and done:
        return "partial_success"
    if failed:
        return "failed"
    if done:
        return "success"
    return "nothing_to_publish"


def run_facebook_phone_batch(
    job: PublishJob,
    *,
    channels: list[str],
    batch_cfg: dict[str, Any],
    dry_run: bool,
    confirm_post: bool,
    execute_channel: Callable[[str], ChannelResult],
    sleep_fn: Callable[[float], None] | None = None,
) -> list[ChannelResult]:
    """Run fb_groups and fb_marketplace sequentially in one PublicationJob."""
    pause = sleep_fn or time.sleep
    ordered = order_facebook_batch_channels(channels, batch_cfg)
    results: list[ChannelResult] = []
    groups_published_this_run = False
    groups_failed_this_run = False

    for ch in ordered:
        state = load_state()
        if is_channel_done(state, job.object_id, ch):
            results.append(
                ChannelResult(
                    channel=ch,
                    ok=True,
                    skipped=True,
                    reason="already published",
                    publication_status="skipped",
                )
            )
            continue

        if (
            ch == "fb_marketplace"
            and groups_failed_this_run
            and batch_cfg.get("stop_on_failure", True)
        ):
            results.append(
                ChannelResult(
                    channel=ch,
                    ok=False,
                    skipped=True,
                    reason="blocked_by_dependency",
                    publication_status="skipped",
                )
            )
            continue

        if (
            ch == "fb_marketplace"
            and groups_published_this_run
            and confirm_post
            and not dry_run
        ):
            pause(float(batch_cfg["delay_between_channels_seconds"]))

        result = execute_channel(ch)
        results.append(result)

        if ch == "fb_groups":
            if result.ok and not result.skipped:
                groups_published_this_run = True
            elif not result.ok and not result.skipped:
                groups_failed_this_run = True

    return results
