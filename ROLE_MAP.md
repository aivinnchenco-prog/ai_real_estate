# ROLE_MAP — карта ролей агентов (временная карта миграции)

> **Статус:** пакетная миграция Agent 6–8 выполнена в `agent_6_qualifier/`; canonical Python-пакеты — `agent6_qualifier`, `agent7_envoy`, `agent8_notary`. Legacy-импорты `agent7.*` и `agent8.*` сохранены как thin compatibility shims.
> Источник правды по **целевой архитектуре** — Notion («Общая структура системы»). Этот файл — карта ролей, canonical-путей и legacy-имён.

---

## 1. Целевые роли

| № | Роль | Папка (цель) | Зона ответственности |
|---|------|--------------|----------------------|
| **4** | **Publisher** | `agent_4_publisher/` | Публикация контента объекта в Telegram и соцсети; служебные поля блокировки и логов в Notion |
| **6** | **Qualifier** | `agent_6_qualifier/` | Диалог с клиентом, квалификация лида, подбор альтернатив, ведение сделки в amoCRM |
| **7** | **Envoy** | `agent_7_envoy/` (цель) | Pre-check календаря, связь с собственником, обновление availability в Notion, возврат условий Qualifier |
| **8** | **Notary** | `agent_8_notary/` (цель) | Генерация документов по брони, прикрепление к сделке amoCRM |

Подробные бизнес-правила Qualifier / Envoy / Notary — в `agent_6_qualifier/AGENT_SPEC.md`.

---

## 2. Фактическое расположение кода (сейчас)

Вся исполняемая логика Agent 6–8 **сосредоточена в одном деплое** `agent_6_qualifier/`:

| Целевая роль | Canonical Python-пакет | Legacy compatibility shims | Точки входа (runtime) |
|--------------|------------------------|----------------------------|------------------------|
| Agent 6 Qualifier | `agent_6_qualifier/src/agent6_qualifier/` | `agent_6_qualifier/src/agent7/` (`agent7.*` — thin re-export shims) | `scripts/start_userbot.sh` → `python3 -m agent6_qualifier.tg_userbot`; legacy: `python3 -m agent7.tg_userbot`; оркестратор `agent6_qualifier.qualifier` |
| Agent 7 Envoy | `agent_6_qualifier/src/agent7_envoy/` | `agent8/` (кроме notary), `agent8/envoy/`, `agent7/airbnb_check.py`, `agent7/owner_registry.py` | `agent7_envoy.auto`, `agent8_run.py`, `owner_reply.py`; вызов из `tg_userbot` при `need_owner_check` |
| Agent 8 Notary | `agent_6_qualifier/src/agent8_notary/` | `agent8/booking_doc.py`, `agent8/notary/`, `agent8/notary_service.py` + `scripts/generate_booking_request.js` | `agent8_notary.booking_doc.generate_booking_doc()` из `tg_userbot` при `booking_confirmed` |

Заглушки-папки (только README, без runtime):

- `agent_7_envoy/` — указатель на код Envoy в `agent_6_qualifier`
- `agent_8_notary/` — указатель на код Notary в `agent_6_qualifier`

**Publisher (Agent 4)** — отдельно в `agent_4_publisher/`; phone-ветка — `agent_4_publisher_social/`.

---

## 3. Legacy-названия (ещё встречаются в коде и документах)

| Legacy-имя | Что означает на самом деле | Где встречается |
|------------|----------------------------|-----------------|
| Python-пакет `agent7` | **legacy compatibility shims** для Agent 6 Qualifier (canonical: `agent6_qualifier`) | `src/agent7/` — thin re-export; импорты в старых тестах, `TG_SESSION=agent7_userbot` |
| Python-пакет `agent8` | **legacy compatibility shims** для Agent 7 Envoy + Agent 8 Notary (canonical: `agent7_envoy`, `agent8_notary`) | `src/agent8/`, `agent8_run.py`, `test_agent8.py` |
| Notion / код `agent6_*` | **Agent 4 Publisher** (не Qualifier) | `agent6_locked`, `agent6_log`, `publisher.json` → `"agent6"`, `notion_gate.agent6_ready()` |
| Документы «Agent 6 Publisher» | **Agent 4 Publisher** по целевой нумерации | `AGENT6_SPEC.md`, `AGENTS.md`, `CHAIN.md`, `MULTI_AGENT.md` |
| «Agent 7 Qualifier» в старых handoff | Устаревшая нумерация; целевая роль — **Agent 6** | `agent_4_publisher/CHAIN.md`, `MULTI_AGENT.md` |
| «Agent 5 Qualifier» | Устаревшая нумерация (архив) | `MEMORY.md`, `FB_MARKETPLACE_PARSER_BRIEF.md` |
| npm `agent6-booking-doc` | **Agent 8 Notary** (документ брони) | `agent_6_qualifier/scripts/package.json` |

---

## 4. Что пока запрещено переименовывать

До отдельного согласованного этапа миграции **не трогать**:

1. **Notion-колонки `agent6_*`** в production CRM (`agent6_locked`, `agent6_carousel_done`, `agent6_video_done`, `agent6_log`, `agent6_mode`, `agent6_taken_at`) — Publisher зависит от точных имён.
2. **Legacy Python-пакеты `agent7` и `agent8`** — не удалять shims; старые импорты должны продолжать работать.
3. **Конфигурацию** `agent_4_publisher/config/publisher.json` (секция `"agent6"`), `schema/notion_schema.json`.
4. **Telethon-сессию** `TG_SESSION=agent7_userbot` (имя файла сессии на сервере).
5. **Persisted session JSON**, форматы amoCRM-стадий, Notion paths, alert component names (`agent7.handler`, `agent8.auto`).

Production scripts и canonical-пакеты используют `agent6_qualifier`, `agent7_envoy`, `agent8_notary`. Legacy `python3 -m agent7.tg_userbot` остаётся рабочим через shim.

---

## 5. Границы ответственности

### Agent 4 — Publisher

- Берёт объект из Notion со статусом готовности к публикации.
- Публикует в Telegram / соцсети, пишет `post_url_*`, выставляет `agent6_locked` и связанные флаги.
- **Не** общается с клиентами-лидами, **не** пишет собственникам, **не** генерирует договоры.

### Agent 6 — Qualifier

- Ведёт диалог с клиентом (TG userbot / в перспективе WA).
- Извлекает `Объект ID`, квалифицирует (даты, бюджет, гости…), подбирает альтернативы.
- Создаёт и обновляет сделку amoCRM; **сам владельцу не пишет** — инициирует Envoy.
- Формулирует ответ клиенту на основе вердикта Envoy; при подтверждённой брони инициирует Notary.

### Agent 7 — Envoy

- Pre-check календаря по колонке `Календарь` в Notion.
- Выбирает канал связи с собственником (WA → TG → Airbnb DM → FB DM).
- Обновляет `availability_status`, `Занято до`, контакты владельца в Notion и amoCRM.
- Возвращает условия Qualifier; не заменяет клиентский диалог.

### Agent 8 — Notary

- По событию «бронь подтверждена» формирует **соглашение о бронировании** (docx) и отправляет клиенту.
- Прикрепляет файл к сделке amoCRM.
- **Полноценный договор аренды / оплаты** — отдельная будущая задача (сейчас не реализован).

---

## 6. Порядок будущей миграции (после документального этапа)

| Этап | Содержание | Критерий готовности |
|------|------------|---------------------|
| **0** ✅ | Документация: `ROLE_MAP.md`, обновление AGENT_SPEC и README | Этот файл |
| **1** ✅ | Canonical `agent6_qualifier` + legacy shims `agent7.*` | `pytest`, userbot стартует |
| **2** ✅ | Логическое разделение `agent8` на `envoy/` и `notary/` с re-export | Тесты Envoy/Notary зелёные |
| **3** ✅ | Shared kernel в `agent6_qualifier` (`models`, `notion_store`, `amo`, `sessions`) | Canonical Envoy/Notary импортируют `agent6_qualifier` |
| **4** ✅ | Canonical пакеты: `agent6_qualifier`, `agent7_envoy`, `agent8_notary` | Production scripts на canonical imports |
| **5** | Физическое выделение `agent_7_envoy/`, `agent_8_notary/` | docker-compose / `sync_env.py` |
| **6** | Publisher: `agent6_*` → `publisher_*` в Notion + коде | Dual-read, миграция production CRM |

Каждый этап — отдельный коммит; после каждого проект должен оставаться запускаемым.

---

## 7. Связанные документы

| Документ | Назначение |
|----------|------------|
| `README.md` | Обзор всех агентов в монорепо |
| `agent_6_qualifier/AGENT_SPEC.md` | Бизнес-правила Agent 6/7/8 |
| `agent_7_envoy/README.md` | Указатель на код Envoy |
| `agent_8_notary/README.md` | Указатель на код Notary |
| `agent_4_publisher/docs/AGENT6_SPEC.md` | Спека Publisher (legacy-имя Agent 6 в заголовке) |
| `schema/notion_schema.json` | Контракт колонок Notion (не менять на этапе 0) |
