# Агент_8 — Notary

Подготовка документов по подтверждённой брони и прикрепление к сделке amoCRM.

## Что уже реализовано

**Соглашение о бронировании (бронь-заявка)** — двуязычный docx с датами, объектом, гостями и порядком следующих шагов.
Это **не** договор аренды и **не** договор оплаты: итоговые условия и оплата оформляются отдельно после показа.

| Компонент | Путь |
|-----------|------|
| Canonical Python | `agent_6_qualifier/src/agent8_notary/` (`booking_doc.py`, `service.py`) |
| Legacy shims | `agent_6_qualifier/src/agent8/booking_doc.py`, `agent8/notary/` |
| Генератор docx (Node) | `agent_6_qualifier/scripts/generate_booking_request.js` |
| npm-зависимости | `agent_6_qualifier/scripts/package.json` |
| Выходные файлы | `agent_6_qualifier/data/contracts/` (в `.gitignore`) |
| Данные агентства | `config/project.json` → `agency` |

## Точка входа (runtime)

При `booking_confirmed` в Agent 6 Qualifier (`agent7/tg_userbot.py`):

1. `agent8_notary.booking_doc.generate_booking_doc()` — создаёт docx *(legacy import: `agent8.booking_doc`)*;
2. файл отправляется клиенту в Telegram;
3. `amo.attach_file(lead_id, doc_path)` — прикрепление к сделке.

## Что ещё не реализовано

- Полноценный **договор аренды** по шаблону (отдельная задача).
- Выделение кода в эту папку `agent_8_notary/` — следующий этап миграции (см. корневой [`ROLE_MAP.md`](../ROLE_MAP.md)).
