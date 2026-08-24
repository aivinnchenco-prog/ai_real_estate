"""Gemini vision fallback when uiautomator selectors miss FB/Android UI."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from .ui import human_pause


@dataclass(frozen=True)
class VisionTap:
    x: int
    y: int
    confidence: float
    label: str = ""


def vision_settings(android_cfg: dict[str, Any]) -> dict[str, Any]:
    ui = android_cfg.get("ui_automation") or {}
    vf = dict(ui.get("vision_fallback") or {})
    return {
        "enabled": bool(vf.get("enabled", False)),
        "min_confidence": float(vf.get("min_confidence", 0.72)),
        "model": str(
            vf.get("model")
            or os.getenv("PUBLISHER_GEMINI_MODEL")
            or os.getenv("GEMINI_MODEL")
            or "gemini-2.5-flash"
        ),
        "max_calls": int(vf.get("max_calls_per_run", 25)),
    }


def _parse_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def get_vision_fallback(android_cfg: dict[str, Any]) -> VisionFallback | None:
    cached = android_cfg.get("_vision_fallback")
    if cached is not None:
        return cached or None
    client = VisionFallback(android_cfg)
    android_cfg["_vision_fallback"] = client if client.available else False
    return client if client.available else None


class VisionFallback:
    def __init__(self, android_cfg: dict[str, Any]):
        self.settings = vision_settings(android_cfg)
        self.android_cfg = android_cfg
        self.api_key = (
            os.getenv("PUBLISHER_GEMINI_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("AGENT9_GEMINI_API_KEY")
            or ""
        )
        self.calls = 0

    @property
    def available(self) -> bool:
        return self.settings["enabled"] and bool(self.api_key)

    def _endpoint(self) -> str:
        model = self.settings["model"]
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.api_key}"
        )

    def _screenshot_b64(self, d) -> str:
        shot = d.screenshot()
        if isinstance(shot, (bytes, bytearray)):
            return base64.b64encode(shot).decode("ascii")
        buf = io.BytesIO()
        shot.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def _call(self, d, prompt: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        if self.calls >= self.settings["max_calls"]:
            return None
        self.calls += 1
        w, h = d.window_size()
        full_prompt = (
            f"{prompt.strip()}\n\n"
            f"Screen size: {w}x{h} pixels. Origin top-left.\n"
            "Return ONLY valid JSON."
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": full_prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": self._screenshot_b64(d),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        try:
            resp = requests.post(self._endpoint(), json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = parts[0].get("text", "")
            return _parse_json(text)
        except Exception:
            return None

    def _ok_tap(self, obj: dict[str, Any] | None) -> VisionTap | None:
        if not obj or not obj.get("found", True):
            if obj and obj.get("found") is False:
                return None
        try:
            confidence = float(obj.get("confidence", 0))
            x = int(obj["x"])
            y = int(obj["y"])
        except (KeyError, TypeError, ValueError):
            return None
        if confidence < self.settings["min_confidence"]:
            return None
        label = str(obj.get("label") or "")
        return VisionTap(x=x, y=y, confidence=confidence, label=label)

    def click_goal(
        self,
        d,
        goal: str,
        *,
        labels: tuple[str, ...] = (),
    ) -> bool:
        hint = ", ".join(labels) if labels else "n/a"
        obj = self._call(
            d,
            (
                "You automate Facebook on Android.\n"
                f"Task: {goal}\n"
                f"Target labels (any language): {hint}\n"
                'Return JSON: {"found": true, "x": 0, "y": 0, '
                '"confidence": 0.0, "label": "..."} '
                'or {"found": false, "confidence": 0}.'
            ),
        )
        tap = self._ok_tap(obj)
        if tap is None:
            return False
        w, h = d.window_size()
        x = max(1, min(tap.x, w - 1))
        y = max(1, min(tap.y, h - 1))
        d.click(x, y)
        human_pause(self.android_cfg, scale=0.35)
        return True

    def open_album(self, d, album: str) -> bool:
        return self.click_goal(
            d,
            f'Open the gallery album/folder named "{album}" (carousel folder for this post).',
            labels=(album, "publisher_social_carousel", "Выбор альбома"),
        )

    def enable_multi_select(self, d) -> bool:
        return self.click_goal(
            d,
            "Enable multi-select mode for choosing several photos in the gallery.",
            labels=(
                "Выбрать несколько",
                "Select multiple",
                "Несколько",
            ),
        )

    def tap_next(self, d) -> bool:
        return self.click_goal(
            d,
            "Tap the Next/Done button to confirm selected photos.",
            labels=("Далее", "Next", "Готово", "Done"),
        )

    def tap_publish(self, d) -> bool:
        return self.click_goal(
            d,
            "Tap the Publish/Post button (blue, bottom-right, ~2cm above the physical bottom edge — not in the gesture/nav bar zone).",
            labels=("Опубликовать", "Post", "Publish"),
        )

    def select_photo_thumbnails(self, d, count: int) -> int:
        count = max(1, min(int(count), 10))
        obj = self._call(
            d,
            (
                "In the photo gallery grid, select photo thumbnails for a carousel post.\n"
                f"Tap exactly {count} distinct photo tiles (not camera, not album headers).\n"
                "If multi-select is off, the first tap may enable it.\n"
                'Return JSON: {"taps": [{"x":0,"y":0,"confidence":0.9}], "count": 0}.'
            ),
        )
        if not obj:
            return 0
        taps_raw = obj.get("taps") or []
        tapped = 0
        w, h = d.window_size()
        for item in taps_raw[:count]:
            try:
                confidence = float(item.get("confidence", 0))
                x = int(item["x"])
                y = int(item["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if confidence < self.settings["min_confidence"]:
                continue
            d.click(max(1, min(x, w - 1)), max(1, min(y, h - 1)))
            tapped += 1
            time.sleep(0.4)
        return tapped
