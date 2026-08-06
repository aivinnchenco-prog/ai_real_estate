#!/usr/bin/env python3
"""Проверка подключения к Supabase. Таблицу создайте через SQL Editor (tasks/schema.sql)."""

import json

import config
from task_queue import check_connection, create_task, fetch_pending


def main():
    print('Supabase URL:', config.SUPABASE_URL or '(не задан)')
    print('Key:', 'задан' if config.SUPABASE_KEY else '(не задан)')
    status = check_connection()
    print(json.dumps(status, ensure_ascii=False, indent=2))

    if not status.get('ok'):
        print('\nЕсли таблицы нет — откройте Supabase → SQL Editor → вставьте tasks/schema.sql → Run')
        return 1

    task_id = create_task(
        title='Тест подключения Supabase',
        brief={'goal': 'Проверка очереди', 'test': True},
        source='setup',
    )
    pending = fetch_pending(limit=3)
    print(f'\nТестовая задача: {task_id}')
    print(f'Pending в очереди: {len(pending)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
