#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from post_reply_agent import (  # noqa: E402
    agent_context,
    build_postmypost_ai_system_prompt,
    object_code_line,
    static_comment_reply,
)


def test_object_code_line():
    cfg = {"postmypost": {"reply_agent": {"enabled": True}}}
    assert object_code_line("20260701_001", cfg) == "🏷 Код объекта: 20260701_001"
    assert object_code_line("", cfg) == ""


def test_static_comment_reply():
    cfg = {
        "postmypost": {
            "reply_agent": {
                "enabled": True,
                "manager_telegram": "@OpenHome_th",
                "comment_reply_template": (
                    "Код: {object_id}. TG: {telegram_channel}{whatsapp_suffix}"
                ),
            }
        }
    }
    text = static_comment_reply("A_20260713_003", cfg)
    assert "A_20260713_003" in text
    assert "@OpenHome_th" in text


def test_ai_prompt_mentions_object_id_formats():
    cfg = {"postmypost": {"reply_agent": {"enabled": True, "manager_telegram": "@OpenHome_th"}}}
    prompt = build_postmypost_ai_system_prompt(cfg)
    assert "YYYYMMDD_NNN" in prompt
    assert "@OpenHome_th" in prompt
    assert "Мой пост" in prompt


def test_whatsapp_suffix():
    cfg = {
        "postmypost": {
            "reply_agent": {"manager_whatsapp": "+66811112222", "manager_telegram": "@x"},
        }
    }
    ctx = agent_context(cfg, object_id="20260701_001")
    assert "WhatsApp" in ctx["whatsapp_suffix"]
