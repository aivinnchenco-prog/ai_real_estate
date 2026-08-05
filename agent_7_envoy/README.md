# Агент_7 — Envoy

Связь с собственником объекта: pre-check календаря, запрос доступности и цены
(WhatsApp / Telegram, при отсутствии — Airbnb / FB директ), перенос контактов в Notion и amoCRM,
возврат условий **Agent 6 Qualifier**.

## Где код сейчас

Runtime пока в монорепо **`agent_6_qualifier/`** (исторические имена пакетов `agent7` / `agent8`):

| Модуль | Путь |
|--------|------|
| Outreach, auto-run, calendar pre-check, разбор ответа владельца | canonical: `agent_6_qualifier/src/agent7_envoy/` (`outreach.py`, `auto.py`, `calendar_check.py`, `owner_result.py`, `owner_handler.py`); legacy shims: `agent8/` |
| Airbnb calendar, реестр владельцев | canonical: `agent7_envoy/airbnb_check.py`, `agent7_envoy/owner_registry.py`; legacy shims: `agent7/airbnb_check.py`, `agent7/owner_registry.py` |
| Owner-шаблоны клиенту/владельцу | `agent_6_qualifier/src/agent7/templates.py` |

## Точки входа

| Способ | Команда / триггер |
|--------|-------------------|
| Автоматически | `Turn.need_owner_check` в Qualifier → `agent7_envoy.auto.auto_outreach()` из `tg_userbot` |
| Ответ владельца | обработчик в `agent7/tg_userbot.py` → `agent7_envoy.owner_handler` / `owner_result` |
| Ручной CLI | `python3 scripts/agent8_run.py --chat CHAT_ID [--send]` *(legacy script name)* |
| Ручной CLI | `python3 scripts/owner_reply.py` |

Выделение в отдельный сервис в папке `agent_7_envoy/` — следующий этап миграции (см. корневой [`ROLE_MAP.md`](../ROLE_MAP.md)).
