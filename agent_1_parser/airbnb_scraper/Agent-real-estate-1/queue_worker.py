"""Фоновый воркер: читает Supabase и готовит задачи для Cursor."""

import asyncio

import config
from CustomLogger import logger
from task_queue import create_task, fetch_pending, use_supabase
import task_runner


def ensure_agent_listener_task():
    """Один раз ставит системную задачу для Cursor-агента."""
    if not use_supabase():
        return ''
    pending = fetch_pending(limit=20)
    marker = 'cursor-agent-listener'
    for task in pending:
        brief = task.get('brief') or {}
        if brief.get('marker') == marker:
            return task.get('id', '')

    return create_task(
        title='Cursor: выполнять задачи из очереди Supabase',
        brief={
            'marker': marker,
            'goal': 'Брать pending-задачи из agent_tasks и реализовывать в коде проекта',
            'steps': [
                'Проверить Supabase → Table Editor → agent_tasks (status=pending)',
                'Прочитать data/task_instructions/<id>.md',
                'Выполнить brief: цель, шаги, критерии готовности',
                'Записать result в Supabase через complete_task',
            ],
            'acceptance': [
                'Код работает, тесты зелёные',
                'Пользователь уведомлён в Telegram',
            ],
            'notes': 'Автозадача: бот опрашивает очередь каждые N секунд',
        },
        project='airbnb-bot',
        source='system',
    )


async def poll_once(bot, admin_ids: list[int]) -> bool:
    if not use_supabase():
        return False

    out = await asyncio.to_thread(task_runner.process_one)
    if not out:
        return False

    task = out['task']
    result = out['result']
    title = task.get('title', 'Задача')
    task_id = task.get('id', '')
    path = result.get('instruction_file', '')

    text = (
        f'📋 <b>Задача для Cursor</b>\n'
        f'{title}\n\n'
        f'ID: <code>{task_id}</code>\n'
        f'📄 <code>{path}</code>\n\n'
        f'Открой файл в Cursor и выполни.'
    )
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:
            logger.warning(f'Не удалось уведомить admin {admin_id}: {exc}')
    return True


async def poll_loop(bot, admin_ids: list[int]):
    logger.info(
        f'Очередь Supabase: опрос каждые {config.TASK_POLL_INTERVAL_SEC} с '
        f'({"вкл" if use_supabase() else "нет ключей — выкл"})'
    )
    while True:
        try:
            await asyncio.sleep(config.TASK_POLL_INTERVAL_SEC)
            await poll_once(bot, admin_ids)
        except asyncio.CancelledError:
            logger.info('Очередь Supabase: опрос остановлен')
            break
        except Exception as exc:
            logger.error(f'Queue poll error: {exc}')
