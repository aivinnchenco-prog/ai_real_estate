"""Очередь задач: Claude планирует → исполнитель (Cursor/скрипт) выполняет."""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

import config
from CustomLogger import logger


def _db_path():
    return config.TASK_QUEUE_DB


def use_supabase() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_KEY)


def _supabase_client():
    from supabase import create_client
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# --- SQLite (локальный fallback) ---

def _sqlite_connect():
    db_path = _db_path()
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_init():
    with _sqlite_connect() as conn:
        conn.execute("""
            create table if not exists agent_tasks (
                id text primary key,
                status text not null default 'pending',
                source text not null default 'user',
                project text not null default 'airbnb-bot',
                title text not null,
                brief text not null default '{}',
                result text,
                error text,
                created_at text not null,
                started_at text,
                finished_at text
            )
        """)
        conn.execute(
            'create index if not exists agent_tasks_status_idx on agent_tasks (status, created_at)'
        )


def init_db():
    if use_supabase():
        return
    _sqlite_init()


def create_task(title: str, brief: dict, project: str = 'airbnb-bot', source: str = 'claude') -> str:
    task_id = str(uuid.uuid4())
    now = _now_iso()

    if use_supabase():
        _supabase_client().table('agent_tasks').insert({
            'id': task_id,
            'status': 'pending',
            'source': source,
            'project': project,
            'title': title,
            'brief': brief,
            'created_at': now,
        }).execute()
    else:
        _sqlite_init()
        with _sqlite_connect() as conn:
            conn.execute(
                """
                insert into agent_tasks (id, status, source, project, title, brief, created_at)
                values (?, 'pending', ?, ?, ?, ?, ?)
                """,
                (task_id, source, project, title, json.dumps(brief, ensure_ascii=False), now),
            )

    logger.info(f'Task created: {task_id} — {title} ({ "supabase" if use_supabase() else "sqlite" })')
    return task_id


def fetch_pending(limit: int = 5) -> list[dict]:
    if use_supabase():
        rows = (
            _supabase_client()
            .table('agent_tasks')
            .select('*')
            .eq('status', 'pending')
            .order('created_at')
            .limit(limit)
            .execute()
            .data
            or []
        )
        return rows

    _sqlite_init()
    with _sqlite_connect() as conn:
        rows = conn.execute(
            """
            select * from agent_tasks
            where status = 'pending'
            order by created_at asc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [_sqlite_row_to_dict(row) for row in rows]


def claim_task(task_id: str) -> dict | None:
    now = _now_iso()

    if use_supabase():
        client = _supabase_client()
        updated = (
            client.table('agent_tasks')
            .update({'status': 'in_progress', 'started_at': now})
            .eq('id', task_id)
            .eq('status', 'pending')
            .execute()
        )
        rows = updated.data or []
        return rows[0] if rows else None

    _sqlite_init()
    with _sqlite_connect() as conn:
        conn.execute(
            "update agent_tasks set status='in_progress', started_at=? where id=? and status='pending'",
            (now, task_id),
        )
        row = conn.execute('select * from agent_tasks where id=?', (task_id,)).fetchone()
    return _sqlite_row_to_dict(row) if row else None


def complete_task(task_id: str, result: dict | None = None, error: str = '') -> bool:
    now = _now_iso()
    status = 'failed' if error else 'done'
    payload = {
        'status': status,
        'result': result or {},
        'error': error or None,
        'finished_at': now,
    }

    if use_supabase():
        _supabase_client().table('agent_tasks').update(payload).eq('id', task_id).execute()
    else:
        _sqlite_init()
        with _sqlite_connect() as conn:
            conn.execute(
                """
                update agent_tasks
                set status=?, result=?, error=?, finished_at=?
                where id=?
                """,
                (status, json.dumps(result or {}, ensure_ascii=False), error, now, task_id),
            )
    return True


def check_connection() -> dict:
    if not use_supabase():
        return {'backend': 'sqlite', 'ok': True, 'path': _db_path()}
    try:
        client = _supabase_client()
        client.table('agent_tasks').select('id').limit(1).execute()
        return {'backend': 'supabase', 'ok': True, 'url': config.SUPABASE_URL}
    except Exception as exc:
        return {'backend': 'supabase', 'ok': False, 'error': str(exc)}


def _sqlite_row_to_dict(row) -> dict:
    data = dict(row)
    for key in ('brief', 'result'):
        if data.get(key):
            try:
                data[key] = json.loads(data[key])
            except json.JSONDecodeError:
                pass
    return data
