"""WhatsApp parser tests."""

from __future__ import annotations

from agent9_connector.notion import whatsapp_write_allowed
from agent9_connector.whatsapp import extract_whatsapp_from_text, normalize_phone


class TestWhatsApp:
    def test_plus66(self):
        r = extract_whatsapp_from_text("+66812345678")
        assert r.valid and r.normalized == "+66812345678"

    def test_thai_local(self):
        r = extract_whatsapp_from_text("0812345678")
        assert r.valid and r.normalized == "+66812345678"

    def test_spaces_dashes(self):
        r = extract_whatsapp_from_text("66 81 234 5678")
        assert r.valid

    def test_wa_me(self):
        r = extract_whatsapp_from_text("https://wa.me/66812345678")
        assert r.valid

    def test_invalid(self):
        r = extract_whatsapp_from_text("call me tomorrow")
        assert not r.valid

    def test_international_preserved(self):
        r = normalize_phone("+14155552671")
        assert r.valid and r.normalized == "+14155552671"

    def test_notion_conflict(self):
        ok, reason = whatsapp_write_allowed("+66111", "+66222")
        assert not ok and reason == "conflict"

    def test_notion_same_ok(self):
        ok, _ = whatsapp_write_allowed("+66111", "+66111")
        assert ok
