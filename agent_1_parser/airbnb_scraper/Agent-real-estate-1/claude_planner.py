"""LLM (Gemini) структурирует задачу пользователя и кладёт в очередь."""

import json
import re
import sys

import config  # noqa: F401 — конфиг подтягивает .env
import gemini_llm
from task_queue import create_task
from CustomLogger import logger


def plan_and_enqueue(user_request: str, project: str = 'airbnb-bot') -> str:
    if not gemini_llm.is_enabled():
        raise RuntimeError('Нужен GEMINI_API_KEY в .env')

    prompt = f"""Ты планировщик задач для кодинг-агента (Cursor).

Запрос пользователя:
{user_request}

Проект: {project}

Разбей на структурированное ТЗ. Ответь ТОЛЬКО JSON:
{{
  "title": "краткое название задачи",
  "goal": "что должно получиться",
  "steps": ["шаг 1", "шаг 2"],
  "files": ["файлы которые трогать"],
  "acceptance": ["критерий готовности 1"],
  "priority": "normal",
  "notes": "важные детали"
}}"""

    raw = gemini_llm.generate(prompt, max_tokens=1500).strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
    brief = json.loads(raw)
    title = brief.get('title') or user_request[:80]
    task_id = create_task(title=title, brief=brief, project=project, source='llm')
    return task_id


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 claude_planner.py "Описание задачи" [project]')
        sys.exit(1)
    request = sys.argv[1]
    proj = sys.argv[2] if len(sys.argv) > 2 else 'airbnb-bot'
    tid = plan_and_enqueue(request, proj)
    print(f'Задача в очереди: {tid}')
