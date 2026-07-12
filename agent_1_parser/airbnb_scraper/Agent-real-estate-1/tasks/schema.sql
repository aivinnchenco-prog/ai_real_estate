-- Supabase / PostgreSQL: очередь задач Claude → Cursor
-- Выполнить в SQL Editor Supabase после создания проекта

create table if not exists agent_tasks (
    id uuid primary key default gen_random_uuid(),
    status text not null default 'pending'
        check (status in ('pending', 'in_progress', 'done', 'failed', 'cancelled')),
    source text not null default 'user',
    project text not null default 'airbnb-bot',
    title text not null,
    brief jsonb not null default '{}',
    result jsonb,
    error text,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz
);

create index if not exists agent_tasks_status_idx on agent_tasks (status, created_at);

alter table agent_tasks enable row level security;

-- Доступ для publishable/anon ключа (бэкенд-скрипты с секретным ключом из .env)
drop policy if exists agent_tasks_backend_all on agent_tasks;
create policy agent_tasks_backend_all on agent_tasks
    for all
    using (true)
    with check (true);
