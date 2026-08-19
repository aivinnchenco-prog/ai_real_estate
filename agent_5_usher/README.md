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

## Social inbound → Agent 6

`agent_5_usher/social_inbound.py` — documented payload contract для комментариев/DM из PostMyPost:

- `build_social_inbound_reference(payload)` → `SocialInboundReference`;
- `resolve_inbound_for_agent6(payload, store=...)` → resolution dict для Qualifier.

Сейчас без webhook: Agent 5 может экспортировать mapping/context для ручной настройки
PostMyPost AI assistant. Реальный inbound требует PostMyPost webhook/API.

## Не в scope Agent 5

- Публикация в соцсети (Agent 4 / PostMyPost / телефон FB);
- Синхронизация URL (`agent_4_publisher/scripts/sync_post_urls.py`).
