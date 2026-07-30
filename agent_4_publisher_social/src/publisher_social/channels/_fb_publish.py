"""Shared Facebook UI helpers for publish confirmation (Groups + Marketplace)."""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from xml.etree import ElementTree as ET

from ..android.ui import human_pause
from .base import ChannelResult

PUBLISH_LABELS = ("Опубликовать", "Post", "Publish")


def parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def publish_button_visible(d, labels: tuple[str, ...] = PUBLISH_LABELS) -> bool:
    for label in labels:
        if (
            d(text=label).exists(timeout=0.2)
            or d(description=label).exists(timeout=0.15)
            or d(textContains=label).exists(timeout=0.15)
        ):
            return True
    return False


def tap_publish_button(
    d,
    android_cfg: dict[str, Any],
    *,
    labels: tuple[str, ...] = PUBLISH_LABELS,
    scroll_attempts: bool = True,
) -> bool:
    try:
        d.hide_keyboard()
    except Exception:
        pass
    human_pause(android_cfg, scale=0.35)
    w, h = d.window_size()
    for attempt in range(5):
        if scroll_attempts and attempt in (1, 3):
            d.swipe(w // 2, int(h * 0.35), w // 2, int(h * 0.72), 0.22)
            human_pause(android_cfg, scale=0.3)
        for label in labels:
            for factory in (
                lambda l=label: d(description=l),
                lambda l=label: d(text=l),
                lambda l=label: d(textContains=l),
            ):
                try:
                    node = factory()
                    if node.exists(timeout=0.35):
                        node.click()
                        return True
                except Exception:
                    continue
        xml = d.dump_hierarchy() or ""
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            label = (a.get("text") or a.get("content-desc") or "").strip()
            if label not in labels:
                continue
            if a.get("clickable") != "true" and a.get("enabled") != "true":
                continue
            parsed = parse_bounds(a.get("bounds", ""))
            if not parsed:
                continue
            d.click((parsed[0] + parsed[2]) // 2, (parsed[1] + parsed[3]) // 2)
            return True
        time.sleep(0.35)
    return False


def confirm_publish_or_stop(
    d,
    *,
    confirm_post: bool,
    android_cfg: dict[str, Any],
    channel: str,
    before_publish: Callable[[], None] | None = None,
    stopped_note: str,
    success_note: str,
) -> ChannelResult:
    if before_publish is not None:
        before_publish()
    if not publish_button_visible(d):
        return ChannelResult(
            channel=channel,
            ok=False,
            reason="publish_button_not_found",
        )
    if not confirm_post:
        return ChannelResult(
            channel=channel,
            ok=True,
            skipped=True,
            reason="stopped_before_publish",
            note=stopped_note,
        )
    if not tap_publish_button(d, android_cfg):
        return ChannelResult(
            channel=channel,
            ok=False,
            reason="publish_button_click_failed",
        )
    human_pause(android_cfg, scale=1.6)
    if publish_button_visible(d):
        if not tap_publish_button(d, android_cfg):
            return ChannelResult(
                channel=channel,
                ok=False,
                reason="publish_button_still_visible",
            )
        human_pause(android_cfg, scale=1.2)
        if publish_button_visible(d):
            return ChannelResult(
                channel=channel,
                ok=False,
                reason="publish_button_still_visible",
            )
    return ChannelResult(channel=channel, ok=True, note=success_note)
