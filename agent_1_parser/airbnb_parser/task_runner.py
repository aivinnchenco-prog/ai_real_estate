"""Читает очередь задач и готовит команды для исполнения (Cursor / скрипты)."""

import json
import os
import sys

import config
from task_queue import claim_task, complete_task, fetch_pending
from CustomLogger import logger

INSTRUCTIONS_DIR = config.TASK_INSTRUCTIONS_DIR


def _format_cursor_prompt(task: dict) -> str:
    brief = task.get('brief') or {}
    lines = [
        f'# Задача: {task.get("title", "")}',
        f'ID: {task.get("id", "")}',
        f'Проект: {task.get("project", "")}',
        '',
        f'## Цель',
        brief.get('goal', ''),
        '',
    ]
    if brief.get('steps'):
        lines.append('## Шаги')
        lines.extend(f'- {step}' for step in brief['steps'])
        lines.append('')
    if brief.get('files'):
        lines.append('## Файлы')
        lines.extend(f'- {path}' for path in brief['files'])
        lines.append('')
    if brief.get('acceptance'):
        lines.append('## Критерии готовности')
        lines.extend(f'- {item}' for item in brief['acceptance'])
        lines.append('')
    if brief.get('notes'):
        lines.append(f'## Заметки\n{brief["notes"]}')
    return '\n'.join(lines).strip()


def process_one(task_id: str | None = None) -> dict | None:
    pending = fetch_pending(limit=1)
    if not pending:
        logger.info('Нет задач в очереди.')
        return None

    task = pending[0]
    if task_id and task['id'] != task_id:
        task = claim_task(task_id)
    else:
        task = claim_task(task['id'])

    if not task:
        return None

    os.makedirs(INSTRUCTIONS_DIR, exist_ok=True)
    prompt_path = os.path.join(INSTRUCTIONS_DIR, f"{task['id']}.md")
    prompt_text = _format_cursor_prompt(task)
    with open(prompt_path, 'w', encoding='utf-8') as f:
        f.write(prompt_text)

    result = {
        'instruction_file': prompt_path,
        'prompt_preview': prompt_text[:500],
        'status': 'ready_for_cursor',
        'hint': f'Открой {prompt_path} в Cursor и выполни задачу.',
    }
    complete_task(task['id'], result=result)
    logger.info(f'Task {task["id"]} → {prompt_path}')
    return {'task': task, 'result': result}


def process_all(limit: int = 5) -> int:
    count = 0
    for _ in range(limit):
        pending = fetch_pending(limit=1)
        if not pending:
            break
        if process_one():
            count += 1
    return count


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--all':
        n = process_all()
        print(f'Обработано задач: {n}')
    else:
        out = process_one()
        if out:
            print(json.dumps(out['result'], ensure_ascii=False, indent=2))
        else:
            print('Очередь пуста.')
