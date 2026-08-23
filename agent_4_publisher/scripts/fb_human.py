"""Человеческие движения мыши/клавиатуры для FB Playwright-веток.

Playwright по умолчанию телепортирует курсор и вставляет текст одним куском.
Здесь — кривая до цели, клик не в центр, набор рывками, паузы «почитал».
"""

from __future__ import annotations

import random
import re
import time
from typing import Any

# Курсор не отдаёт Playwright — помним сами, чтобы следующий ход был непрерывным.
_cursor: dict[str, float] = {}

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""


def reset_cursor() -> None:
    _cursor.clear()


def human_delay(cfg: dict[str, Any], factor: float = 1.0) -> None:
    hd = cfg.get("browser", {}).get("human_delay_ms", {})
    lo = int(hd.get("min", 900)) / 1000.0
    hi = int(hd.get("max", 2400)) / 1000.0
    time.sleep(random.uniform(lo, hi) * factor)


def bezier_path(
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int,
) -> list[tuple[float, float]]:
    """Квадратичная Безье со случайной контрольной точкой — не прямая линия."""
    steps = max(3, int(steps))
    x0, y0 = start
    x2, y2 = end
    mid_x = (x0 + x2) / 2 + random.uniform(-80, 80)
    mid_y = (y0 + y2) / 2 + random.uniform(-60, 60)
    points: list[tuple[float, float]] = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1.0 - t
        x = u * u * x0 + 2 * u * t * mid_x + t * t * x2
        y = u * u * y0 + 2 * u * t * mid_y + t * t * y2
        points.append((x, y))
    return points


def _viewport(page: Any) -> tuple[int, int]:
    vp = getattr(page, "viewport_size", None) or {}
    return int(vp.get("width") or 1440), int(vp.get("height") or 900)


def _cursor_xy(page: Any) -> tuple[float, float]:
    w, h = _viewport(page)
    if "x" not in _cursor:
        _cursor["x"] = random.uniform(w * 0.2, w * 0.8)
        _cursor["y"] = random.uniform(h * 0.2, h * 0.7)
    return _cursor["x"], _cursor["y"]


def human_mouse_move(page: Any, x: float, y: float, *, steps: int | None = None) -> None:
    n = steps if steps is not None else random.randint(12, 28)
    start = _cursor_xy(page)
    mouse = page.mouse
    try:
        for px, py in bezier_path(start, (x, y), n):
            mouse.move(px, py)
            _cursor["x"], _cursor["y"] = px, py
            if random.random() < 0.12:
                time.sleep(random.uniform(0.008, 0.03))
    except Exception:
        try:
            mouse.move(x, y, steps=max(5, n // 2))
            _cursor["x"], _cursor["y"] = x, y
        except Exception:
            pass


def wander_mouse(page: Any) -> None:
    """Случайный ход «глянул в сторону» — без клика."""
    w, h = _viewport(page)
    x = random.uniform(80, max(120, w - 80))
    y = random.uniform(80, max(120, h - 80))
    human_mouse_move(page, x, y, steps=random.randint(8, 16))


def idle_scroll(page: Any, cfg: dict[str, Any], seconds_range: tuple[int, int] = (15, 45)) -> None:
    """Полистать ленту: скролл + редкие движения мыши, как при чтении."""
    total = random.uniform(*seconds_range)
    deadline = time.time() + total
    try:
        while time.time() < deadline:
            delta = random.randint(280, 1100)
            if random.random() < 0.18:
                delta = -random.randint(160, 520)
            page.mouse.wheel(0, delta)
            if random.random() < 0.45:
                wander_mouse(page)
            time.sleep(random.uniform(1.2, 5.5))
            if random.random() < 0.12:
                human_delay(cfg, 0.4)
    except Exception:
        pass


def human_click(page: Any, locator: Any, cfg: dict[str, Any] | None = None) -> None:
    """Навести курсор по кривой, чуть промахнуться, кликнуть не в геометрический центр."""
    try:
        locator.scroll_into_view_if_needed()
    except Exception:
        pass
    box = None
    try:
        box = locator.bounding_box()
    except Exception:
        box = None
    if not box or box.get("width", 0) < 2 or box.get("height", 0) < 2:
        locator.click()
        return
    x = box["x"] + box["width"] * random.uniform(0.28, 0.72)
    y = box["y"] + box["height"] * random.uniform(0.28, 0.72)
    if random.random() < 0.4:
        human_mouse_move(
            page,
            x + random.uniform(-16, 16),
            y + random.uniform(-10, 10),
            steps=random.randint(8, 14),
        )
        time.sleep(random.uniform(0.04, 0.16))
    human_mouse_move(page, x, y)
    if cfg:
        human_delay(cfg, 0.22)
    else:
        time.sleep(random.uniform(0.08, 0.22))
    try:
        page.mouse.click(x, y)
        _cursor["x"], _cursor["y"] = x, y
    except Exception:
        locator.click()


def type_like_human(page: Any, text: str, cfg: dict[str, Any] | None = None) -> None:
    """Набор рывками по 1–4 слова, с паузами на знаках и редким «отвёл мышь»."""
    if not text:
        return
    tokens = re.findall(r"\S+\s*|\s+", text)
    if not tokens:
        page.keyboard.insert_text(text)
        return
    i = 0
    while i < len(tokens):
        n = random.randint(1, 4)
        chunk = "".join(tokens[i : i + n])
        page.keyboard.insert_text(chunk)
        i += n
        stripped = chunk.rstrip()
        if stripped.endswith((".", "!", "?", "…")) or "\n" in chunk:
            time.sleep(random.uniform(0.35, 1.4))
        else:
            time.sleep(random.uniform(0.04, 0.22))
        if random.random() < 0.07:
            wander_mouse(page)


def type_into(page: Any, locator: Any, text: str, cfg: dict[str, Any] | None = None) -> None:
    """Клик по полю и человеческий набор. Короткий текст — тоже рывками, не fill()."""
    human_click(page, locator, cfg)
    try:
        locator.fill("")
    except Exception:
        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
        except Exception:
            pass
    type_like_human(page, str(text), cfg)


def read_before_submit(page: Any, cfg: dict[str, Any]) -> None:
    """Пауза «перечитал объявление» перед Publish — не сразу после загрузки фото."""
    if random.random() < 0.7:
        wander_mouse(page)
    human_delay(cfg, random.uniform(1.4, 2.4))
    if random.random() < 0.35:
        try:
            page.mouse.wheel(0, random.randint(-180, 220))
        except Exception:
            pass
        human_delay(cfg, 0.5)
