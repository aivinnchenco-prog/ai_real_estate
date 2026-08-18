"""Shared Facebook UI helpers for publish confirmation (Groups + Marketplace)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal
from xml.etree import ElementTree as ET

from ..android.ui import human_pause
from .base import ChannelResult

PUBLISH_LABELS = ("Опубликовать", "Post", "Publish")
UPLOADING_MARKERS = (
    "загруз",
    "uploading",
    "отправк",
    "processing",
    "подождите",
)
PublishPlacement = Literal["top_toolbar", "bottom_right"]


def parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _label_matches(label: str, labels: tuple[str, ...]) -> bool:
    clean = label.strip()
    if not clean:
        return False
    low = clean.lower()
    for item in labels:
        if clean == item or item.lower() in low:
            return True
    return False


@dataclass(frozen=True)
class PublishTarget:
    bounds: tuple[int, int, int, int]
    enabled: bool
    clickable: bool


def collect_publish_targets(
    xml: str,
    labels: tuple[str, ...] = PUBLISH_LABELS,
) -> list[PublishTarget]:
    targets: list[PublishTarget] = []
    for node in ET.fromstring(xml).iter("node"):
        label = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        if not _label_matches(label, labels):
            continue
        parsed = parse_bounds(node.attrib.get("bounds", ""))
        if not parsed:
            continue
        enabled = node.attrib.get("enabled", "true") == "true"
        clickable = node.attrib.get("clickable", "false") == "true"
        targets.append(
            PublishTarget(bounds=parsed, enabled=enabled, clickable=clickable)
        )
    return targets


def pick_publish_target(
    targets: list[PublishTarget],
    *,
    screen_h: int,
    screen_w: int,
    placement: PublishPlacement = "bottom_right",
) -> PublishTarget | None:
    if not targets:
        return None

    if placement == "bottom_right":
        bottom = [
            t
            for t in targets
            if t.bounds[1] >= int(screen_h * 0.62) or t.bounds[3] >= int(screen_h * 0.72)
        ]
        pool = bottom or targets
        enabled = [t for t in pool if t.enabled] or pool
        clickable = [t for t in enabled if t.clickable] or enabled
        return max(clickable, key=lambda t: (t.bounds[2], t.bounds[3]))

    toolbar = [t for t in targets if t.bounds[3] <= int(screen_h * 0.28)]
    pool = toolbar or targets
    enabled = [t for t in pool if t.enabled] or pool
    clickable = [t for t in pool if t.clickable] or pool
    return min(clickable, key=lambda t: (t.bounds[1], t.bounds[0]))


def uploads_still_running(xml: str) -> bool:
    low = xml.lower()
    return any(marker in low for marker in UPLOADING_MARKERS)


def prepare_publish_screen(
    d,
    android_cfg: dict[str, Any],
    *,
    placement: PublishPlacement = "bottom_right",
) -> None:
    try:
        d.hide_keyboard()
    except Exception:
        pass
    human_pause(android_cfg, scale=0.5)
    w, h = d.window_size()
    if placement == "bottom_right":
        for _ in range(4):
            d.swipe(w // 2, int(h * 0.82), w // 2, int(h * 0.42), 0.22)
            time.sleep(0.25)
        return
    for _ in range(3):
        d.swipe(w // 2, int(h * 0.28), w // 2, int(h * 0.68), 0.22)
        time.sleep(0.25)


def wait_for_publish_ready(
    d,
    android_cfg: dict[str, Any],
    *,
    timeout_sec: float = 120,
    labels: tuple[str, ...] = PUBLISH_LABELS,
    placement: PublishPlacement = "bottom_right",
) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        prepare_publish_screen(d, android_cfg, placement=placement)
        xml = d.dump_hierarchy() or ""
        if uploads_still_running(xml):
            time.sleep(2.0)
            continue
        targets = collect_publish_targets(xml, labels)
        if any(t.enabled or t.clickable for t in targets):
            return True
        if publish_button_visible(d, labels):
            return True
        time.sleep(2.0)
    return publish_button_visible(d, labels)


def publish_button_visible(d, labels: tuple[str, ...] = PUBLISH_LABELS) -> bool:
    for label in labels:
        if (
            d(text=label).exists(timeout=0.2)
            or d(description=label).exists(timeout=0.15)
            or d(descriptionContains=label).exists(timeout=0.15)
            or d(textContains=label).exists(timeout=0.15)
        ):
            return True
    return False


def _tap_bottom_right_fallback(d, w: int, h: int) -> bool:
    for x_ratio, y_ratio in ((0.90, 0.86), (0.86, 0.88), (0.92, 0.84)):
        d.click(int(w * x_ratio), int(h * y_ratio))
        time.sleep(0.35)
        if not publish_button_visible(d):
            return True
    return False


def tap_publish_button(
    d,
    android_cfg: dict[str, Any],
    *,
    labels: tuple[str, ...] = PUBLISH_LABELS,
    scroll_attempts: bool = True,
    placement: PublishPlacement = "bottom_right",
) -> bool:
    try:
        d.hide_keyboard()
    except Exception:
        pass
    human_pause(android_cfg, scale=0.45)
    w, h = d.window_size()
    for attempt in range(6):
        if scroll_attempts:
            if placement == "bottom_right":
                d.swipe(w // 2, int(h * 0.82), w // 2, int(h * 0.42), 0.22)
            elif attempt in (0, 2, 4):
                d.swipe(w // 2, int(h * 0.28), w // 2, int(h * 0.68), 0.22)
            else:
                d.swipe(w // 2, int(h * 0.68), w // 2, int(h * 0.32), 0.24)
            human_pause(android_cfg, scale=0.25)

        for label in labels:
            for factory in (
                lambda l=label: d(description=l),
                lambda l=label: d(text=l),
                lambda l=label: d(descriptionContains=l),
                lambda l=label: d(textContains=l),
            ):
                try:
                    node = factory()
                    if not node.exists(timeout=0.35):
                        continue
                    if placement == "bottom_right":
                        info = node.info or {}
                        bounds = info.get("bounds") or {}
                        top = int(bounds.get("top", 0))
                        if top and top < int(h * 0.55):
                            continue
                    node.click()
                    return True
                except Exception:
                    continue

        xml = d.dump_hierarchy() or ""
        target = pick_publish_target(
            collect_publish_targets(xml, labels),
            screen_h=h,
            screen_w=w,
            placement=placement,
        )
        if target is not None:
            x1, y1, x2, y2 = target.bounds
            d.click((x1 + x2) // 2, (y1 + y2) // 2)
            return True

        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            label = (a.get("text") or a.get("content-desc") or "").strip()
            if not _label_matches(label, labels):
                continue
            parsed = parse_bounds(a.get("bounds", ""))
            if not parsed:
                continue
            if placement == "bottom_right" and parsed[1] < int(h * 0.55):
                continue
            d.click((parsed[0] + parsed[2]) // 2, (parsed[1] + parsed[3]) // 2)
            return True

        if placement == "bottom_right" and attempt >= 3:
            if _tap_bottom_right_fallback(d, w, h):
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
    publish_wait_sec: float = 120,
    publish_placement: PublishPlacement = "bottom_right",
) -> ChannelResult:
    if before_publish is not None:
        before_publish()
    if not wait_for_publish_ready(
        d,
        android_cfg,
        timeout_sec=publish_wait_sec,
        placement=publish_placement,
    ):
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
    if not tap_publish_button(
        d,
        android_cfg,
        placement=publish_placement,
    ):
        return ChannelResult(
            channel=channel,
            ok=False,
            reason="publish_button_click_failed",
        )
    human_pause(android_cfg, scale=1.6)
    if publish_button_visible(d):
        if not tap_publish_button(d, android_cfg, placement=publish_placement):
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
