# Агент_7 — Envoy

Связь с собственником объекта: pre-check календаря, запрос доступности и цены
(WhatsApp / Telegram, при отсутствии — Airbnb / FB директ), перенос контактов в Notion и amoCRM,
возврат условий **Агенту_6 Qualifier**.

## Где код сейчас

Runtime пока в монорепо **`agent_6_qualifier/`** (исторические имена пакетов):

| Модуль | Путь |
|--------|------|
| Outreach, auto-run, calendar pre-check, разбор ответа владельца | `agent_6_qualifier/src/agent8/` (`outreach.py`, `auto.py`, `calendar_check.py`, `owner_result.py`) |
| Airbnb calendar, реестр владельцев, owner-шаблоны | `agent_6_qualifier/src/agent7/` (`airbnb_check.py`, `owner_registry.py`, `templates.py`) |

## Точки входа

| Способ | Команда / триггер |
|--------|-------------------|
| Автоматически | `Turn.need_owner_check` в Qualifier → `agent8.auto.auto_outreach()` из `tg_userbot` |
| Ответ владельца | обработчик в `agent7/tg_userbot.py` → `agent8.owner_result` |
| Ручной CLI | `python3 scripts/agent8_run.py --chat CHAT_ID [--send]` |
| Ручной CLI | `python3 scripts/owner_reply.py` |

Выделение в отдельный сервис в папке `agent_7_envoy/` — следующий этап миграции (см. корневой [`ROLE_MAP.md`](../ROLE_MAP.md)).
