# Агент_5 #Usher

После успешной публикации через PostMyPost фиксирует intent запуска **PostMyPost AI Agent** (ответы на комментарии / DM-воронка).

## Текущий статус

| Компонент | Статус |
|-----------|--------|
| PostMyPost AI Agent trigger (сеть) | **pending** — `pending_postmypost_ai_agent` |
| Внутренний adapter boundary | `postmypost_ai_agent.py` |
| ChatPlace | **удалён** из production flow |

## Граница интеграции

`agent_5_usher/postmypost_ai_agent.py`:

- `queue_postmypost_ai_agent(...)` — идемпотентная постановка в очередь после публикации;
- `integration_ready()` — `False` до появления реального API-триггера PostMyPost;
- state: `agent_5_usher/data/postmypost_ai_agent_state.json`.

Вызов из Agent 4: `publish_pipeline.spawn_agent5_postmypost_ai_agent()` после успешной PostMyPost-публикации.

## Не в scope Agent 5

- Публикация в соцсети (Agent 4 / PostMyPost / телефон FB);
- Синхронизация URL (`agent_4_publisher/scripts/sync_post_urls.py`).
