"""Security and logging tests."""

from __future__ import annotations

import json
import os

from agent9_connector.gemini import ALLOWED_INTENTS, sanitize_interpretation
from agent9_connector.logging_util import log_event


class TestSecurity:
    def test_api_key_not_logged(self, tmp_path, monkeypatch):
        import agent9_connector.config_loader as cfg
        import agent9_connector.logging_util as lu
        monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(lu, "data_dir", lambda: tmp_path)
        monkeypatch.setenv("AGENT9_GEMINI_API_KEY", "SECRET_KEY_XYZ")
        log_event("test", message="error SECRET_KEY_XYZ leaked")
        line = (tmp_path / "logs" / "connector.jsonl").read_text()
        assert "SECRET_KEY_XYZ" not in line

    def test_malformed_output_safe(self):
        i = sanitize_interpretation({"intent": "send_money", "confidence": "high"})
        assert i.intent in ALLOWED_INTENTS

    def test_only_allowed_enums(self):
        from agent9_connector.gemini import ALLOWED_QUESTION_TYPES, ALLOWED_ROLES
        i = sanitize_interpretation({"intent": "question", "question_type": "evil", "role": "hacker"})
        assert i.question_type == "other"
        assert i.role in ALLOWED_ROLES
        assert i.intent in ALLOWED_INTENTS
        assert "evil" not in ALLOWED_QUESTION_TYPES
