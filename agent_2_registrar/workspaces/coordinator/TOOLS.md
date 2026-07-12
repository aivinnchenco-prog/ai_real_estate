# Local Tools — Coordinator

## PROJECT_ROOT

Абсолютный путь к корню проекта Real Estate Agent.
При деплое через `scripts/setup.sh` подставляется автоматически.
По умолчанию: родительская директория от `workspaces/coordinator/`.

Все пути к данным: `{PROJECT_ROOT}/data/...`

## Sub-agents

- `sessions_spawn` с `agentId`: parser | crm | video | publisher
- `runTimeoutSeconds`: parser 300, crm 180, video 900, publisher 600
- Результаты приходят через announce — не опрашивай в цикле

## Skills (coordinator)

Нет локальных skills — делегирует worker-агентам.
