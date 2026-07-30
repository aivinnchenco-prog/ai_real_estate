from __future__ import annotations

import random
import time
from typing import Any


def connect_device(android_cfg: dict[str, Any]):
    import uiautomator2 as u2

    serial = android_cfg.get("serial")
    if serial:
        return u2.connect(serial)
    return u2.connect()


def ensure_unlocked(d) -> None:
    """Разбудить и смахнуть lockscreen (иначе dump_hierarchy пустой / AOD)."""
    try:
        d.screen_on()
    except Exception:
        pass
    try:
        d.unlock()
    except Exception:
        pass
    try:
        d.shell("wm dismiss-keyguard")
    except Exception:
        pass
    time.sleep(0.4)
    w, h = d.window_size()
    for _ in range(6):
        xml = ""
        try:
            xml = d.dump_hierarchy() or ""
        except Exception:
            pass
        locked = (
            "keyguard" in xml.lower()
            or "проведите пальцем" in xml
            or "Чтобы открыть" in xml
            or "Swipe to unlock" in xml
            or "Swipe up to open" in xml
        )
        if not locked:
            return
        d.swipe(w // 2, int(h * 0.88), w // 2, int(h * 0.15), 0.18)
        time.sleep(1.0)


def human_pause(android_cfg: dict[str, Any], *, scale: float = 1.0) -> None:
    delay = android_cfg.get("human_delay_ms") or {}
    lo = int(delay.get("min", 1200))
    hi = int(delay.get("max", 3200))
    if hi < lo:
        hi = lo
    time.sleep(random.uniform(lo, hi) / 1000.0 * scale)


def dismiss_permissions(d, timeout: float = 2.0) -> None:
    labels = (
        "При использовании приложения",
        "Только в этот раз",
        "Разрешить",
        "Allow",
        "While using the app",
        "Allow all",
        "ALLOW",
    )
    for _ in range(3):
        clicked = False
        for label in labels:
            if d(text=label).exists(timeout=0.4):
                d(text=label).click()
                time.sleep(1)
                clicked = True
                break
        if not clicked:
            break


def pass_threads_human_check(d, *, timeout: float = 1.5) -> bool:
    """
    Threads challenge «подтвердите, что вы — человек»:
    достаточно один раз нажать «Продолжить».
    """
    challenged = d(textContains="подтвердите, что вы").exists(timeout=timeout) or d(
        descriptionContains="подтвердите, что вы"
    ).exists(timeout=0.3)
    if not challenged:
        return False
    if d(text="Продолжить").exists(timeout=1.5):
        d(text="Продолжить").click()
        time.sleep(1.5)
        return True
    if d(description="Продолжить").exists(timeout=0.5):
        d(description="Продолжить").click()
        time.sleep(1.5)
        return True
    return False


def click_text_or_desc(d, label: str, timeout: float = 5.0) -> bool:
    if d(text=label).exists(timeout=timeout):
        d(text=label).click()
        return True
    if d(description=label).exists(timeout=0.5):
        d(description=label).click()
        return True
    return False


def click_center(d, bounds: dict[str, int]) -> None:
    cx = (bounds["left"] + bounds["right"]) // 2
    cy = (bounds["top"] + bounds["bottom"]) // 2
    d.click(cx, cy)
