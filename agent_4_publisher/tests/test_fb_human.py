#!/usr/bin/env python3
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fb_human as h  # noqa: E402


class FakeMouse:
    def __init__(self) -> None:
        self.moves: list[tuple[float, float]] = []
        self.clicks: list[tuple[float, float]] = []
        self.wheels: list[tuple[int, int]] = []

    def move(self, x, y, steps=None):
        self.moves.append((float(x), float(y)))

    def click(self, x, y):
        self.clicks.append((float(x), float(y)))

    def wheel(self, dx, dy):
        self.wheels.append((int(dx), int(dy)))


class FakeKeyboard:
    def __init__(self) -> None:
        self.inserted: list[str] = []
        self.keys: list[str] = []

    def insert_text(self, text: str) -> None:
        self.inserted.append(text)

    def press(self, key: str) -> None:
        self.keys.append(key)


class FakeLocator:
    def __init__(self, box: dict | None) -> None:
        self._box = box
        self.clicked = False
        self.filled: str | None = None

    def scroll_into_view_if_needed(self) -> None:
        return None

    def bounding_box(self):
        return self._box

    def click(self) -> None:
        self.clicked = True

    def fill(self, value: str) -> None:
        self.filled = value


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.viewport_size = {"width": 1440, "height": 900}


def test_bezier_path_curves_and_lands_on_target():
    random.seed(7)
    start, end = (10.0, 10.0), (400.0, 300.0)
    pts = h.bezier_path(start, end, 16)
    assert len(pts) == 16
    assert math.hypot(pts[-1][0] - end[0], pts[-1][1] - end[1]) < 1e-6
    # хотя бы одна точка заметно не на прямой start→end
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    off = []
    for x, y in pts[:-1]:
        t = ((x - start[0]) * dx + (y - start[1]) * dy) / (length * length)
        px, py = start[0] + t * dx, start[1] + t * dy
        off.append(math.hypot(x - px, y - py))
    assert max(off) > 1.0


def test_human_click_moves_then_clicks_inside_box(monkeypatch):
    h.reset_cursor()
    monkeypatch.setattr(h.time, "sleep", lambda *_a, **_k: None)
    page = FakePage()
    loc = FakeLocator({"x": 100, "y": 50, "width": 80, "height": 40})
    h.human_click(page, loc, {"browser": {"human_delay_ms": {"min": 1, "max": 2}}})
    assert page.mouse.clicks
    cx, cy = page.mouse.clicks[-1]
    assert 100 <= cx <= 180
    assert 50 <= cy <= 90
    assert len(page.mouse.moves) >= 3
    assert loc.clicked is False


def test_human_click_falls_back_without_box():
    h.reset_cursor()
    page = FakePage()
    loc = FakeLocator(None)
    h.human_click(page, loc)
    assert loc.clicked is True
    assert page.mouse.clicks == []


def test_type_like_human_inserts_in_bursts(monkeypatch):
    h.reset_cursor()
    monkeypatch.setattr(h.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(h, "wander_mouse", lambda *_a, **_k: None)
    page = FakePage()
    text = "Phuket villa near the beach. Two bedrooms."
    h.type_like_human(page, text, {})
    assert "".join(page.keyboard.inserted) == text
    assert len(page.keyboard.inserted) >= 2


def test_groups_pipeline_reexports_human_helpers():
    import fb_groups_pipeline as fb

    assert fb.human_click is h.human_click
    assert fb.type_into is h.type_into
    assert fb.read_before_submit is h.read_before_submit
