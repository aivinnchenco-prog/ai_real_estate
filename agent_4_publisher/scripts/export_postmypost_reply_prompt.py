#!/usr/bin/env python3
"""Вывести промпт агента «Мой пост» для PostMyPost Automation (UI)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from post_reply_agent import (  # noqa: E402
    build_postmypost_ai_system_prompt,
    static_comment_reply,
)
from publish_pipeline import load_config, load_dotenv  # noqa: E402


def main() -> int:
    load_dotenv()
    config = load_config()
    parser = argparse.ArgumentParser(description="Export PostMyPost «Мой пост» AI prompt")
    parser.add_argument("--object-id", default="20260701_001", help="Пример кода для static reply")
    parser.add_argument("--json", action="store_true", help="JSON вместо текста")
    args = parser.parse_args()

    payload = {
        "agent_name": "Мой пост",
        "postmypost_setup": [
            "PostMyPost → Automation → +",
            "Триггер: событие в соцсети → новый комментарий ИЛИ новое сообщение (Direct)",
            "Блок: AI assistant — вставить system_prompt ниже",
            "Блок: Social interaction → отправить ответ пользователю",
            "Сохранить — одна automation на все сети и все посты",
        ],
        "system_prompt": build_postmypost_ai_system_prompt(config),
        "static_reply_example": static_comment_reply(args.object_id, config),
        "note": (
            "В подписи каждого поста пайплайн уже ставит «Код объекта: …» — "
            "AI читает его из контекста поста. Настройка UI — один раз."
        ),
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("=== PostMyPost Automation: агент «Мой пост» ===\n")
        print("Шаги настройки (один раз):")
        for i, step in enumerate(payload["postmypost_setup"], 1):
            print(f"  {i}. {step}")
        print("\n--- System prompt (AI assistant) ---\n")
        print(payload["system_prompt"])
        print("\n--- Пример фиксированного ответа (без AI) ---\n")
        print(payload["static_reply_example"])
        print(f"\n({payload['note']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
