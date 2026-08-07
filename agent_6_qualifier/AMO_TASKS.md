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

`scripts/amo_task_worker.py` — idempotent one-shot reconciliation (not a daemon).

### Staging/production wiring

```bash
sudo bash deploy/install_amo_task_worker.sh
```

Installs:

- `deploy/amo-task-worker.service` — `Type=oneshot`
- `deploy/amo-task-worker.timer` — every **10 minutes**, `Persistent=true` (runs after reboot)

Manual/cron entrypoint:

```bash
bash scripts/run_amo_task_worker.sh
```

### Staging diagnostics (read-only)

```bash
python3 scripts/amo_tasks_diagnose.py
```

Uses only `GET /api/v4/account?with=task_types` and `GET /api/v4/users`.
Does not create leads/tasks or mutate pipeline/contacts. Never prints access token.

### Worker startup validation

When `AMO_TASKS_ENABLED=true`, worker exits non-zero on:

- missing `AMO_ACCESS_TOKEN` or `AMO_SUBDOMAIN`
- invalid `AMO_TASK_TYPE_ID` env value

Agent 6 message runtime is **not** blocked by missing optional task config.

## Config contract

| Setting | Required | Fallback | If missing |
|---------|----------|----------|------------|
| `AMO_TASKS_ENABLED` | no | `true` from `config/amo_tasks.json` | tasks disabled when `false`/`0` |
| `AMO_SUBDOMAIN` | yes (when tasks enabled) | — | worker exit 1; amo client fails at runtime |
| `AMO_ACCESS_TOKEN` | yes (when tasks enabled) | — | worker exit 1; amo client fails at runtime |
| `AMO_TASK_TYPE_ID` | no | `task_type_ids.default` in json (default `1`) | uses json fallback |
| `AMO_DEFAULT_RESPONSIBLE_USER_ID` | no | lead `responsible_user_id` via GET lead | `ensure_task` logs `responsible_user_id missing` |
| `AMO_MANAGER_RESPONSIBLE_USER_ID` | no | `AMO_DEFAULT_RESPONSIBLE_USER_ID` | same as default env |
| SLA hours/minutes | no | `config/amo_tasks.json` | built-in defaults (2h/24h/15m) |

`asyncio.create_task` in Python handlers is **not** amoCRM task API.

Overdue policy:

- `owner_followup` overdue → Telegram manager alert + `ensure need_human` (once)
- `client_followup` overdue → manager review event (max 1 auto follow-up policy in config)

Escalation markers: `data/amo_task_state.json`

## API endpoints used

- `GET /api/v4/tasks`
- `POST /api/v4/tasks`
- `PATCH /api/v4/tasks/{id}`
- `GET /api/v4/account?with=task_types` (diagnostics)
- `GET /api/v4/users` (diagnostics)
- (existing) leads, contacts, notes, pipelines

## resolve_next_action

`amo_next_action.resolve_next_action(stage, open_tasks)` — need_human has highest priority.
