"""Playwright DOM helpers for Facebook Marketplace Messenger (Agent 8)."""

from __future__ import annotations

import re
import time
from typing import Any

MESSAGE_BUTTON = re.compile(
    r"message|написать|написати|send message|сообщени|повідом",
    re.I,
)
SEND_BUTTON = re.compile(r"send|отправ|enter|надісл", re.I)
COMPOSER = re.compile(r"message|сообщени|write|напис|повідом", re.I)


def detect_security_state(url: str, body_text: str = "") -> str | None:
    u = (url or "").lower()
    t = (body_text or "").lower()
    if "login" in u and "facebook.com" in u:
        return "LOGIN_REQUIRED"
    if "checkpoint" in u or "security" in u or "captcha" in t:
        return "CHECKPOINT"
    if "temporarily blocked" in t or "ограничен" in t:
        return "ACCOUNT_PAUSED"
    return None


def _iter_roots(page: Any):
    yield page
    for frame in page.frames:
        if frame != page.main_frame:
            yield frame


def find_message_button(page: Any) -> Any | None:
    for root in _iter_roots(page):
        try:
            btn = root.get_by_role("button", name=MESSAGE_BUTTON)
            if btn.count() > 0:
                return btn.first
        except Exception:
            pass
        try:
            link = root.get_by_role("link", name=MESSAGE_BUTTON)
            if link.count() > 0:
                return link.first
        except Exception:
            pass
    return None


def find_composer(page: Any) -> Any | None:
    for root in _iter_roots(page):
        for locator in (
            root.get_by_role("textbox", name=COMPOSER),
            root.locator('[contenteditable="true"][role="textbox"]'),
            root.locator('[contenteditable="true"][data-lexical-editor="true"]'),
            root.locator('[aria-label*="Message" i]'),
            root.locator('[aria-label*="сообщени" i]'),
            root.locator('[data-lexical-editor="true"]'),
        ):
            try:
                if locator.count() > 0 and locator.first.is_visible():
                    return locator.first
            except Exception:
                continue
    return None


def wait_for_composer(page: Any, *, timeout_s: float = 8.0) -> Any | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        composer = find_composer(page)
        if composer is not None:
            return composer
        time.sleep(0.5)
    return None


def find_send_button(page: Any) -> Any | None:
    for root in _iter_roots(page):
        try:
            btn = root.get_by_role("button", name=SEND_BUTTON)
            if btn.count() > 0:
                return btn.last
        except Exception:
            pass
    return None


def click_message_button(page: Any, *, timeout_ms: int = 15000) -> bool:
    btn = find_message_button(page)
    if btn is None:
        return False
    btn.click(timeout=timeout_ms)
    return wait_for_composer(page) is not None


def type_and_send(page: Any, text: str, *, timeout_ms: int = 15000) -> bool:
    composer = wait_for_composer(page) or find_composer(page)
    if composer is None:
        return False
    composer.click(timeout=timeout_ms)
    try:
        composer.fill(text)
    except Exception:
        page.keyboard.type(text, delay=20)
    send = find_send_button(page)
    if send is not None:
        try:
            send.click(timeout=3000)
            return True
        except Exception:
            pass
    composer.press("Enter")
    time.sleep(1)
    return True
