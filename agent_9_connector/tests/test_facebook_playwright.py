"""Unit tests for Playwright helper functions (no real browser)."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent9_connector.connectors import facebook_playwright as pw


class TestPlaywrightHelpers:
    def test_detect_login(self):
        assert pw.detect_security_state("https://www.facebook.com/login") == "LOGIN_REQUIRED"

    def test_detect_checkpoint(self):
        assert pw.detect_security_state("https://facebook.com/checkpoint") == "CHECKPOINT"

    def test_bind_thread_id(self):
        from agent9_connector.connectors.facebook import FacebookConnector
        tid, _ = FacebookConnector.bind_thread("1", "https://facebook.com/messages/t/555")
        assert tid == "555"

    def test_find_message_button_role(self):
        page = MagicMock()
        btn = MagicMock()
        btn.count.return_value = 1
        btn.first = "button-locator"
        page.get_by_role.return_value = btn
        page.frames = []
        assert pw.find_message_button(page) == "button-locator"

    def test_find_composer_contenteditable(self):
        page = MagicMock()
        empty = MagicMock()
        empty.count.return_value = 0
        page.get_by_role.return_value = empty
        loc = MagicMock()
        loc.count.return_value = 1
        first = MagicMock()
        first.is_visible.return_value = True
        loc.first = first
        page.locator.return_value = loc
        page.frames = []
        assert pw.find_composer(page) is first
