# amoCRM operational tasks (not asyncio.create_task)

## Task keys

| Key | SLA | Created when | Completed when |
|-----|-----|--------------|----------------|
| `owner_followup` | 2h | Agent 7 успешно отправил запрос владельцу (TG auto) | Ответ владельца free/busy/conditions_changed; stage reconciliation |
| `client_followup` | 24h | Agent 6 исходящее с `awaiting_client_response=true` | Следующее inbound сообщение клиента |
| `need_human` | 15m (config) | Handoff к менеджеру | **Manual** — менеджер закрывает в amoCRM |

Prefix in task text: `[OPENHOME:<key>]`

## Dedup

`lead_id + task_key` → максимум одна открытая задача (`ensure_task`).

## Overdue

`scripts/amo_task_worker.py` — idempotent cron worker:

- `owner_followup` overdue → Telegram manager alert + `ensure need_human` (once)
- `client_followup` overdue → manager review event (max 1 auto follow-up policy in config)

Escalation markers: `data/amo_task_state.json`

## Config

`config/amo_tasks.json` + env:

- `AMO_TASKS_ENABLED`
- `AMO_DEFAULT_RESPONSIBLE_USER_ID`
- `AMO_MANAGER_RESPONSIBLE_USER_ID`
- `AMO_TASK_TYPE_ID`

## API endpoints used

- `GET /api/v4/tasks`
- `POST /api/v4/tasks`
- `PATCH /api/v4/tasks/{id}`
- (existing) leads, contacts, notes, pipelines

## resolve_next_action

`amo_next_action.resolve_next_action(stage, open_tasks)` — need_human has highest priority.
